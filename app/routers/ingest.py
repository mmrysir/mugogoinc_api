from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.models.business_unit import BusinessUnit
from app.models.lead import AbandonedLead, CrmForwardStatus, DeliveryStatus, Lead
from app.schemas.lead import IngestResponse, LeadIngestRequest
from app.services.attribution import sync_exclusion_audience
from app.services.deduplication import compute_dedupe_hash, find_duplicate
from app.services.google_ads import upload_offline_conversion
from app.tasks.routing import dispatch_lead

logger = logging.getLogger("mugogo.ingest")

router = APIRouter(prefix="/ingest", tags=["Ingestion"])

# Simple in-memory rate limiter (per IP + business unit)
_RATE_STORE: dict[str, list[float]] = {}
_RATE_WINDOW = 60.0  # seconds
_RATE_MAX = 30  # max requests per window

api_key_header = APIKeyHeader(name="X-Mugogo-Api-Key", auto_error=True)


async def get_api_key(api_key: str = Security(api_key_header)):
    expected_key = settings.cloudflare_worker_secret
    if api_key != expected_key:
        logger.warning("[INGEST] Security rejection: Invalid Cloudflare Worker API key provided")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    return api_key


def _rate_key(client_ip: str, bu_id: str) -> str:
    return f"{client_ip}:{bu_id}"


def _enforce_rate_limit(client_ip: str, bu_id: str) -> None:
    import time

    key = _rate_key(client_ip, bu_id)
    now = time.time()
    lst = _RATE_STORE.get(key, [])
    lst = [t for t in lst if now - t <= _RATE_WINDOW]
    if len(lst) >= _RATE_MAX:
        raise HTTPException(status_code=429, detail="Too many requests")
    lst.append(now)
    _RATE_STORE[key] = lst


@router.post(
    "/{business_unit_id}",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Universal Lead Ingestion Endpoint",
)
async def ingest_lead(
    business_unit_id: str,
    body: LeadIngestRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    api_key: str = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    """
    Universal sink for all inbound lead payloads.

    1. Validate payload (Pydantic + E.164 phone).
    2. Compute dedupe_hash = SHA256(phone_number + business_unit_id).
    3. Check 24h deduplication window.
    4. Write record with attribution (gclid, fbclid, UTMs).
    5. Dispatch async routing and Meta/Google CAPI retargeting exclusion tasks.
    """
    # ── 1. Resolve BusinessUnit ───────────────────────────────────────────────
    bu_result = await db.execute(
        select(BusinessUnit).where(BusinessUnit.id == business_unit_id)
    )
    business_unit = bu_result.scalar_one_or_none()
    if not business_unit:
        raise HTTPException(status_code=404, detail="Business Unit not found")

    geo_data = body.geo_metadata
    user_agent_header = request.headers.get("user-agent") or (geo_data.user_agent if geo_data else None)

    # Extract attribution parameters
    payload_meta = body.payload_meta or {}
    gclid = body.gclid or payload_meta.get("gclid")
    fbclid = body.fbclid or payload_meta.get("fbclid")
    utm_source = body.utm_source or payload_meta.get("utm_source")
    utm_medium = body.utm_medium or payload_meta.get("utm_medium")
    utm_campaign = body.utm_campaign or payload_meta.get("utm_campaign")

    if body.event_type in ["partial_submission", "partial_abandoned"]:
        abandoned = AbandonedLead(
            id=str(uuid.uuid4()),
            business_unit_id=business_unit_id,
            email=body.email,
            phone_number=body.phone_number,
            ip_address=geo_data.ip_address if geo_data else None,
            country=geo_data.country if geo_data else None,
            city=geo_data.city if geo_data else None,
            user_agent=user_agent_header,
            created_at=datetime.now(timezone.utc),
        )
        db.add(abandoned)
        await db.flush()
        return IngestResponse(
            lead_id=abandoned.id,
            is_duplicate=False,
            message="Partial lead recorded.",
            event_type=body.event_type,
        )

    # ── 2. Deduplication & Rate Limiting ────────────────────────────────────
    client_ip = request.client.host if request.client else "unknown"
    try:
        _enforce_rate_limit(client_ip, business_unit_id)
    except HTTPException:
        logger.warning("[INGEST] Rate limit exceeded for ip=%s bu=%s", client_ip, business_unit_id)
        raise

    dedupe_hash = await compute_dedupe_hash(body.phone_number, business_unit_id)
    existing = await find_duplicate(db, dedupe_hash)

    if existing:
        logger.info(
            "[INGEST] 24h spam guard hit — duplicate phone within window: lead_id=%s phone=%s bu=%s",
            existing.id,
            body.phone_number,
            business_unit_id,
        )
        return IngestResponse(
            lead_id=existing.id,
            is_duplicate=True,
            message="Duplicate submission detected within 24-hour window. No action taken.",
        )

    # ── 2b. Perpetual Returning Lead Lookup (uses ix_leads_dedupe_hash) ───────
    # Passes only when no match was found in the 24h window above.
    # Finds any historical lead with the same phone+BU hash, regardless of age.
    returning_stmt = (
        select(Lead)
        .where(Lead.dedupe_hash == dedupe_hash)
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    returning_result = await db.execute(returning_stmt)
    returning_lead = returning_result.scalar_one_or_none()

    if returning_lead:
        # Re-engagement path: bump counters and surface to top of Lead Feed
        returning_lead.submission_count += 1
        returning_lead.is_returning = True
        returning_lead.last_activity_at = datetime.now(timezone.utc)
        await db.flush()
        logger.info(
            "[INGEST] Returning lead re-engaged — lead_id=%s phone=%s count=%d bu=%s",
            returning_lead.id,
            returning_lead.phone_number,
            returning_lead.submission_count,
            business_unit_id,
        )
        return IngestResponse(
            lead_id=returning_lead.id,
            is_duplicate=False,
            message=f"Returning lead updated. Submission count: {returning_lead.submission_count}.",
            event_type="returning_submission",
        )

    # ── 3. Persist Lead ───────────────────────────────────────────────────────
    crm_status = (
        CrmForwardStatus.NOT_REQUIRED
        if business_unit.route_type.value == "EXPRESS"
        else CrmForwardStatus.QUEUED
    )

    lead = Lead(
        id=str(uuid.uuid4()),
        business_unit_id=business_unit_id,
        source_channel=body.source_channel,
        full_name=body.full_name,
        phone_number=body.phone_number,
        email=body.email,
        payload_meta=payload_meta,
        delivery_status=DeliveryStatus.PENDING,
        crm_forward_status=crm_status,
        ip_address=geo_data.ip_address if geo_data else client_ip,
        country=geo_data.country if geo_data else None,
        city=geo_data.city if geo_data else None,
        user_agent=user_agent_header,
        gclid=gclid,
        fbclid=fbclid,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        dedupe_hash=dedupe_hash,
        submission_count=1,
        is_returning=False,
        last_activity_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(lead)
    await db.flush()

    logger.info(
        "[INGEST] New lead ingested — id=%s name=%s phone=%s channel=%s bu=%s gclid=%s fbclid=%s",
        lead.id,
        lead.full_name,
        lead.phone_number,
        lead.source_channel.value,
        business_unit_id,
        gclid,
        fbclid,
    )

    # ── 4. Dispatch Background Routing & Attribution Tasks ───────────────────
    lead_id = lead.id
    bu_snapshot = business_unit

    async def _route() -> None:
        async with AsyncSessionLocal() as task_db:
            lead_result = await task_db.execute(select(Lead).where(Lead.id == lead_id))
            task_lead = lead_result.scalar_one_or_none()
            if task_lead:
                await dispatch_lead(task_db, task_lead, bu_snapshot)

    background_tasks.add_task(_route)

    # Dispatch Server-Side Retargeting & Exclusion Audience Sync
    background_tasks.add_task(
        sync_exclusion_audience,
        lead_phone=lead.phone_number,
        lead_email=lead.email,
        gclid=gclid,
        fbclid=fbclid,
    )
    
    if lead.gclid:
        background_tasks.add_task(
            upload_offline_conversion,
            gclid=lead.gclid,
            conversion_time=lead.created_at
        )

    return IngestResponse(
        lead_id=lead.id,
        is_duplicate=False,
        message="Lead accepted, routing dispatched, and retargeting signals synced.",
        event_type="full_submission",
    )
