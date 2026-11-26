"""Business Unit scoped Marketing Health Audit endpoints.

Endpoints (all under /organizations/{org_id}/business-units/{unit_id}):
  PATCH  /audit-profile          → update BU audit profile fields
  POST   /audits                 → run a 7-pillar audit (pre-filled from BU)
  GET    /audits                 → chronological audit history with score deltas
  GET    /audits/{audit_id}      → dual-view report (client_summary + agent_diagnostic)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit import AuditLead
from app.models.business_unit import BusinessUnit
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.routers.auth import get_current_user
from app.routers.organizations import _can_access
from app.schemas.audit import AuditRunRequest
from app.schemas.business_unit import BusinessUnitResponse, BusinessUnitUpdate
from app.services.audit_diagnostic import AEO_PILLAR_NAME, WebsiteAuditScanner

logger = logging.getLogger("mugogo.bu_audit")

router = APIRouter(
    prefix="/organizations/{org_id}/business-units/{unit_id}",
    tags=["Business Unit Audits"],
    dependencies=[Depends(get_current_user)],
)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _load_org_and_bu(
    org_id: str, unit_id: str, db: AsyncSession, user: User
) -> tuple[Organization, BusinessUnit]:
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")
    if not await _can_access(user, org, db):
        raise HTTPException(status_code=403, detail="Access denied to this organization.")

    bu = await db.get(BusinessUnit, unit_id)
    if not bu or bu.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Business unit not found in this organization.")
    return org, bu


def _effective_url(bu: BusinessUnit, override: Optional[str] = None) -> str:
    if override:
        return override
    if bu.website_url:
        return bu.website_url
    if bu.primary_url:
        return bu.primary_url
    if bu.google_search_console_site:
        return bu.google_search_console_site
    return ""


def _effective_city(bu: BusinessUnit, override: Optional[str] = None) -> str:
    if override:
        return override
    return bu.city or ""


def _effective_industry(bu: BusinessUnit, override: Optional[str] = None) -> str:
    if override:
        return override
    return bu.industry or "General Business"


def _effective_business_name(bu: BusinessUnit, override: Optional[str] = None) -> str:
    if override:
        return override
    return bu.name


def _effective_phone(bu: BusinessUnit, override: Optional[str] = None) -> Optional[str]:
    if override is not None:
        return override
    return bu.phone


def _build_client_summary(report_data: dict, previous_score: Optional[float], baseline_score: Optional[float] = None) -> dict:
    """Simplified executive metrics for client/org_owner roles."""
    pillars = report_data.get("pillars", [])
    # Map 7 technical pillars -> 3 executive metrics + AI discoverability
    seo = next((p for p in pillars if p["name"] == "Search Engine Optimization"), None)
    reputation = next((p for p in pillars if p["name"] == "Reputation & Reviews"), None)
    conversion = next((p for p in pillars if p["name"] == "Content & Conversion Funnel"), None)
    gbp = next((p for p in pillars if p["name"] == "Google Business Profile"), None)
    aeo = next((p for p in pillars if p["name"] == AEO_PILLAR_NAME), None)

    visibility_pillars = [p["score"] for p in (seo, gbp, aeo) if p]
    online_visibility = round(sum(visibility_pillars) / len(visibility_pillars), 1) if visibility_pillars else 0
    reputation_trust = reputation["score"] if reputation else 0
    lead_capture = conversion["score"] if conversion else 0
    ai_discoverability = aeo["score"] if aeo else report_data.get("aeo_score", 0)

    overall = report_data.get("scores", {}).get("overall", 0.0)
    delta = None
    if previous_score is not None:
        delta = round(overall - previous_score, 1)
    baseline_delta = None
    if baseline_score is not None:
        baseline_delta = round(overall - baseline_score, 1)

    # Top 3 plain-English priorities from action plan
    action_plan = report_data.get("action_plan", [])
    top_priorities = []
    for a in action_plan[:3]:
        top_priorities.append({
            "title": a.get("title", ""),
            "evidence": a.get("evidence", ""),
            "impact": a.get("impact", ""),
            "priority": a.get("priority", ""),
        })

    return {
        "online_visibility": online_visibility,
        "reputation_and_trust": reputation_trust,
        "lead_capture": lead_capture,
        "ai_discoverability": ai_discoverability,
        "overall_score": overall,
        "grade": report_data.get("grade", "F"),
        "score_delta": delta,
        "baseline_score": baseline_score,
        "baseline_delta": baseline_delta,
        "top_priorities": top_priorities,
        "competitor_rating": report_data.get("metrics", {}).get("google_rating"),
        "competitor_reviews": report_data.get("metrics", {}).get("google_reviews"),
        "competitor_median_reviews": report_data.get("metrics", {}).get("competitor_median_reviews"),
    }


# ── Audit Profile Update ──────────────────────────────────────────────────────


# ── Unified profile save ─────────────────────────────────────────────────────


@router.patch(
    "",
    response_model=BusinessUnitResponse,
    summary="Unified tenant-scoped Business Unit profile update",
)
async def update_business_unit_full_profile(
    org_id: str,
    unit_id: str,
    payload: BusinessUnitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessUnitResponse:
    """Persist every editable Business Profile field in one tenant-scoped call.

    Only fields present in the request body are written. Storefront rating and
    review count are intentionally rejected here — they are owned exclusively by
    the Google Places sync / audit pipeline and must never be hand-edited.
    """
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges to edit business unit profiles",
        )

    _org, bu = await _load_org_and_bu(org_id, unit_id, db, current_user)

    import json as _json

    updates = payload.model_dump(exclude_unset=True)
    updates.pop("google_business_rating", None)
    updates.pop("google_business_review_count", None)

    # Detect GBP identifier changes to prevent stale metadata leakage
    gbp_changed = False
    if "google_business_place_id" in updates and updates["google_business_place_id"] != bu.google_business_place_id:
        gbp_changed = True
    if "google_business_map_url" in updates and updates["google_business_map_url"] != bu.google_business_map_url:
        gbp_changed = True

    for field, value in updates.items():
        setattr(bu, field, value)

    # Clear stale GBP metadata when identifiers change
    if gbp_changed:
        bu.google_business_rating = None
        bu.google_business_review_count = None
        if bu.digital_footprint_metrics:
            try:
                footprint = (
                    _json.loads(bu.digital_footprint_metrics)
                    if isinstance(bu.digital_footprint_metrics, str)
                    else dict(bu.digital_footprint_metrics)
                )
                footprint.pop("google_business", None)
                bu.digital_footprint_metrics = _json.dumps(footprint) if footprint else None
            except Exception:
                bu.digital_footprint_metrics = None

    await db.commit()
    await db.refresh(bu)
    return bu


class AuditProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    website_url: Optional[str] = Field(None, max_length=512)
    city: Optional[str] = Field(None, max_length=255)
    industry: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


@router.patch("/audit-profile", response_model=BusinessUnitResponse, summary="Update BU audit profile fields")
async def update_audit_profile(
    org_id: str,
    unit_id: str,
    payload: AuditProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessUnitResponse:
    org, bu = await _load_org_and_bu(org_id, unit_id, db, current_user)

    if payload.name is not None:
        bu.name = payload.name
    if payload.website_url is not None:
        bu.website_url = payload.website_url
        bu.primary_url = payload.website_url
    if payload.city is not None:
        bu.city = payload.city
    if payload.industry is not None:
        bu.industry = payload.industry
    if payload.phone is not None:
        bu.phone = payload.phone
        bu.owner_whatsapp_number = payload.phone

    await db.commit()
    await db.refresh(bu)
    return bu


# ── Run Audit ─────────────────────────────────────────────────────────────────


class RunAuditRequest(BaseModel):
    url: Optional[str] = None
    business_name: Optional[str] = None
    city: Optional[str] = None
    industry: Optional[str] = None
    phone: Optional[str] = None
    save_to_profile: bool = False
    is_monthly_snapshot: bool = False


@router.post("/audits", summary="Run a 7-pillar audit for this business unit")
async def run_bu_audit(
    org_id: str,
    unit_id: str,
    payload: RunAuditRequest = RunAuditRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    org, bu = await _load_org_and_bu(org_id, unit_id, db, current_user)

    # Resolve effective values (request overrides > BU profile)
    url = _effective_url(bu, payload.url)
    if not url:
        raise HTTPException(status_code=400, detail="No website URL available. Set website_url on the business unit or pass url in the request.")
    business_name = _effective_business_name(bu, payload.business_name)
    city = _effective_city(bu, payload.city)
    industry = _effective_industry(bu, payload.industry)
    phone = _effective_phone(bu, payload.phone)

    # Optionally persist profile updates
    if payload.save_to_profile:
        if payload.url:
            bu.website_url = payload.url
            bu.primary_url = payload.url
        if payload.city is not None:
            bu.city = payload.city
        if payload.industry is not None:
            bu.industry = payload.industry
        if payload.phone is not None:
            bu.phone = payload.phone
            bu.owner_whatsapp_number = payload.phone
        if payload.business_name is not None:
            bu.name = payload.business_name
        await db.flush()

    # Check if GBP is agent/admin-verified (locked)
    import json as _json

    metrics = bu.digital_footprint_metrics
    if isinstance(metrics, str):
        try:
            metrics = _json.loads(metrics)
        except Exception:
            metrics = {}
    if not isinstance(metrics, dict):
        metrics = {}
    gbp_meta = metrics.get("gbp") or {}
    is_verified = bool(gbp_meta.get("verified"))

    scanner = WebsiteAuditScanner(
        target_url=url,
        business_name=business_name,
        primary_city=city,
        industry=industry,
        phone=phone,
        google_place_id=bu.google_business_place_id,
        maps_url=bu.google_business_map_url,
        gbp_verified=is_verified,
    )
    report = await scanner.run_full_diagnostic()
    overall_score = float(report.get("scores", {}).get("overall", 0.0))

    # If verified, refresh rating & review count from the locked Place ID
    if is_verified and bu.google_business_place_id:
        from app.services.places import fetch_google_place_details

        place_data = await fetch_google_place_details(place_id=bu.google_business_place_id)
        if place_data:
            if place_data.get("rating") is not None:
                bu.google_business_rating = place_data["rating"]
            if place_data.get("review_count") is not None:
                bu.google_business_review_count = place_data["review_count"]
        gbp_meta["rating"] = bu.google_business_rating
        gbp_meta["review_count"] = bu.google_business_review_count
        metrics["gbp"] = gbp_meta
        bu.digital_footprint_metrics = metrics
        await db.flush()

    lead = AuditLead(
        business_name=business_name,
        target_url=report.get("url", url),
        email=bu.owner_email,
        industry=industry,
        overall_score=overall_score,
        report_data=report,
        organization_id=org_id,
        business_unit_id=unit_id,
        triggered_by_user_id=current_user.id,
        is_monthly_snapshot=payload.is_monthly_snapshot,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    return {
        "id": lead.id,
        "business_unit_id": unit_id,
        "organization_id": org_id,
        "created_at": lead.created_at.isoformat(),
        "overall_score": overall_score,
        "grade": report.get("grade", "F"),
        "report_data": report,
    }


# ── Audit History ─────────────────────────────────────────────────────────────


@router.get("/audits", summary="List audit history for this business unit")
async def list_bu_audits(
    org_id: str,
    unit_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    org, bu = await _load_org_and_bu(org_id, unit_id, db, current_user)

    result = await db.execute(
        select(AuditLead)
        .where(AuditLead.business_unit_id == unit_id)
        .order_by(AuditLead.created_at.desc())
    )
    audits = result.scalars().all()

    # Compute score deltas
    history = []
    prev_score = None
    for a in reversed(audits):  # oldest first for delta calc
        delta = None
        if prev_score is not None:
            delta = round(a.overall_score - prev_score, 1)
        history.append({
            "id": a.id,
            "created_at": a.created_at.isoformat(),
            "overall_score": a.overall_score,
            "grade": (a.report_data or {}).get("grade", "F"),
            "score_delta": delta,
        })
        prev_score = a.overall_score

    history.reverse()  # newest first
    return history


# ── Dual-View Audit Report ────────────────────────────────────────────────────


@router.get("/audits/{audit_id}", summary="Get audit report (client_summary + agent_diagnostic)")
async def get_bu_audit(
    org_id: str,
    unit_id: str,
    audit_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    org, bu = await _load_org_and_bu(org_id, unit_id, db, current_user)

    result = await db.execute(
        select(AuditLead).where(
            AuditLead.id == audit_id,
            AuditLead.business_unit_id == unit_id,
        )
    )
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Audit report not found.")

    report_data = lead.report_data or {}

    # Previous score for delta
    prev_result = await db.execute(
        select(AuditLead)
        .where(
            AuditLead.business_unit_id == unit_id,
            AuditLead.created_at < lead.created_at,
        )
        .order_by(AuditLead.created_at.desc())
        .limit(1)
    )
    prev_lead = prev_result.scalar_one_or_none()
    previous_score = prev_lead.overall_score if prev_lead else None

    # Two-tier model: prefer the most recent monthly snapshot as the stable
    # baseline; fall back to the first audit when no snapshot exists yet.
    snapshot_result = await db.execute(
        select(AuditLead)
        .where(
            AuditLead.business_unit_id == unit_id,
            AuditLead.is_monthly_snapshot == True,
            AuditLead.id != lead.id,
        )
        .order_by(AuditLead.created_at.desc())
        .limit(1)
    )
    snapshot_lead = snapshot_result.scalar_one_or_none()
    if snapshot_lead is not None:
        baseline_score = snapshot_lead.overall_score
    else:
        first_result = await db.execute(
            select(AuditLead)
            .where(AuditLead.business_unit_id == unit_id)
            .order_by(AuditLead.created_at.asc())
            .limit(1)
        )
        first_lead = first_result.scalar_one_or_none()
        baseline_score = first_lead.overall_score if first_lead else None

    client_summary = _build_client_summary(report_data, previous_score, baseline_score)

    # Role-aware: agents/admins get full diagnostic; clients get summary only
    is_agent = current_user.role in (UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT)

    response: Dict[str, Any] = {
        "id": lead.id,
        "business_unit_id": unit_id,
        "organization_id": org_id,
        "created_at": lead.created_at.isoformat(),
        "overall_score": lead.overall_score,
        "grade": report_data.get("grade", "F"),
        "client_summary": client_summary,
    }

    if is_agent:
        response["agent_diagnostic"] = {
            "pillars": report_data.get("pillars", []),
            "action_plan": report_data.get("action_plan", []),
            "metrics": report_data.get("metrics", {}),
            "competitors": report_data.get("competitors", []),
            "gbp_profile": report_data.get("gbp_profile", {}),
            "findings": report_data.get("findings", []),
            "scores": report_data.get("scores", {}),
            "aeo_score": report_data.get("aeo_score", 0),
            "aeo_checks": report_data.get("aeo_checks", []),
        }

    return response
