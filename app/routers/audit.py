"""Public Website Audit & Lead Magnet endpoints.

Endpoints:
  POST /api/v1/audit/run    → Run the marketing diagnostic + store the lead
  GET  /api/v1/audit/{id}   → Retrieve a previously generated audit report
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit import AuditLead
from app.schemas.audit import AuditLeadResponse, AuditReport, AuditRunRequest, AuditRunResponse
from app.services.audit_diagnostic import WebsiteAuditScanner

logger = logging.getLogger("mugogo.audit")

router = APIRouter(prefix="/audit", tags=["Website Audit & Lead Magnet"])


@router.post("/run", response_model=AuditRunResponse, summary="Run website marketing diagnostic")
async def run_audit(
    payload: AuditRunRequest,
    db: AsyncSession = Depends(get_db),
) -> AuditRunResponse:
    """Run the full multi-pillar marketing diagnostic for a prospect's URL and
    persist the resulting lead + report. Public (lead magnet) endpoint.
    """
    scanner = WebsiteAuditScanner()
    diagnostic = await scanner.run_full_diagnostic(payload.url)

    pillars = diagnostic.get("pillars") or []
    overall_score = float(diagnostic.get("overall_score") or 0.0)

    # Persist the audit lead
    lead = AuditLead(
        business_name=payload.business_name.strip(),
        target_url=payload.url.strip(),
        email=payload.email.strip().lower(),
        industry=payload.industry.strip() if payload.industry else None,
        overall_score=overall_score,
        report_data={
            "url": diagnostic.get("url"),
            "final_url": diagnostic.get("final_url"),
            "status_code": diagnostic.get("status_code"),
            "fetch_error": diagnostic.get("fetch_error"),
            "pillars": pillars,
        },
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    audit_id = lead.id
    report = AuditReport(
        audit_id=audit_id,
        url=payload.url.strip(),
        business_name=lead.business_name,
        industry=lead.industry,
        overall_score=overall_score,
        pillars=[
            {
                "name": p.get("name"),
                "score": float(p.get("score") or 0.0),
                "checks": p.get("checks") or [],
            }
            for p in pillars
        ],
        generated_at=lead.created_at,
        status="completed",
    )

    return AuditRunResponse(
        status="completed",
        audit=report,
        lead=AuditLeadResponse(
            id=lead.id,
            business_name=lead.business_name,
            target_url=lead.target_url,
            email=lead.email,
            industry=lead.industry,
            overall_score=lead.overall_score,
            created_at=lead.created_at,
        ),
    )


@router.get("/{audit_id}", response_model=AuditReport, summary="Retrieve a generated audit report")
async def get_audit(
    audit_id: int,
    db: AsyncSession = Depends(get_db),
) -> AuditReport:
    """Retrieve a previously generated audit report by ID (for sharing / email follow-ups)."""
    result = await db.execute(select(AuditLead).where(AuditLead.id == audit_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Audit report not found.")

    pillars = lead.report_data.get("pillars") or []
    return AuditReport(
        audit_id=lead.id,
        url=lead.target_url,
        business_name=lead.business_name,
        industry=lead.industry,
        overall_score=float(lead.overall_score or 0.0),
        pillars=[
            {
                "name": p.get("name"),
                "score": float(p.get("score") or 0.0),
                "checks": p.get("checks") or [],
            }
            for p in pillars
        ],
        generated_at=lead.created_at,
        status="completed",
    )
