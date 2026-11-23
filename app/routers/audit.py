"""Public Website Audit & Lead Magnet endpoints (v2).

Endpoints:
  POST /api/v1/audit/run    → Run the marketing diagnostic + store the lead
  GET  /api/v1/audit/{id}   → Retrieve a previously generated audit report
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit import AuditLead
from app.schemas.audit import AuditRunRequest, AuditRunResponse
from app.services.audit_diagnostic import WebsiteAuditScanner

logger = logging.getLogger("mugogo.audit")

router = APIRouter(prefix="/audit", tags=["Website Audit & Lead Magnet"])


@router.post("/run", response_model=AuditRunResponse, summary="Run website marketing diagnostic")
async def run_marketing_audit(
    payload: AuditRunRequest,
    db: AsyncSession = Depends(get_db),
) -> AuditRunResponse:
    """Run the full v2 multi-pillar marketing diagnostic for a prospect's URL
    and persist the resulting lead + report. Public (lead magnet) endpoint.
    """
    scanner = WebsiteAuditScanner(
        target_url=payload.url,
        business_name=payload.business_name,
        primary_city=payload.primary_city or "",
        industry=payload.industry or "General Business",
    )
    report = await scanner.run_full_diagnostic()
    overall_score = float(report.get("scores", {}).get("overall", 0.0))

    lead = AuditLead(
        business_name=payload.business_name.strip(),
        target_url=report.get("url", payload.url.strip()),
        email=payload.email.strip().lower(),
        industry=payload.industry.strip() if payload.industry else None,
        overall_score=overall_score,
        report_data=report,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return lead


@router.get("/{audit_id}", response_model=AuditRunResponse, summary="Retrieve a generated audit report")
async def get_marketing_audit(
    audit_id: int,
    db: AsyncSession = Depends(get_db),
) -> AuditRunResponse:
    """Retrieve a previously generated audit report by ID (for sharing / email follow-ups)."""
    result = await db.execute(select(AuditLead).where(AuditLead.id == audit_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Audit report not found")
    return lead
