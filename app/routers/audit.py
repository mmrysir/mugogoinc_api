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
from app.schemas.audit import (
    AuditCheck,
    AuditFinding,
    AuditPillar,
    AuditReport,
    AuditRunRequest,
    AuditRunResponse,
)
from app.services.audit_diagnostic import WebsiteAuditScanner

logger = logging.getLogger("mugogo.audit")

router = APIRouter(prefix="/audit", tags=["Website Audit & Lead Magnet"])


def _build_pillars_from_findings(
    findings: list[Dict[str, Any]],
) -> tuple[list[AuditPillar], Dict[str, Any]]:
    """Group flat findings by pillar and compute per-pillar + overall scores."""
    grouped: Dict[str, list[Dict[str, Any]]] = {}
    for f in findings or []:
        pillar = str(f.get("pillar") or "General")
        grouped.setdefault(pillar, []).append(f)

    pillars: list[AuditPillar] = []
    for pillar_name, checks in grouped.items():
        passed = sum(1 for c in checks if c.get("passed"))
        score = round((passed / len(checks)) * 100, 2) if checks else 0.0
        pillars.append(
            AuditPillar(
                name=pillar_name,
                score=score,
                checks=[
                    AuditCheck(
                        name=str(c.get("check") or "check"),
                        passed=bool(c.get("passed")),
                        detail=c.get("details"),
                    )
                    for c in checks
                ],
            )
        )

    scores = {
        "overall": round(
            sum(float(p.score or 0.0) for p in pillars) / len(pillars) if pillars else 0.0, 2
        ),
        "passed_count": sum(1 for f in findings or [] if f.get("passed")),
        "total_count": len(findings or []),
    }

    return pillars, scores


@router.post("/run", response_model=AuditRunResponse, summary="Run website marketing diagnostic")
async def run_audit(
    payload: AuditRunRequest,
    db: AsyncSession = Depends(get_db),
) -> AuditRunResponse:
    """Run the full multi-pillar marketing diagnostic for a prospect's URL and
    persist the resulting lead + report. Public (lead magnet) endpoint.
    """
    scanner = WebsiteAuditScanner(payload.url)
    diagnostic = await scanner.run_full_diagnostic()

    findings: list[Dict[str, Any]] = diagnostic.get("findings") or []
    pillars, scores = _build_pillars_from_findings(findings)
    overall_score = float(scores["overall"])

    # Persist the audit lead
    lead = AuditLead(
        business_name=payload.business_name.strip(),
        target_url=payload.url.strip(),
        email=payload.email.strip().lower(),
        industry=payload.industry.strip() if payload.industry else None,
        overall_score=overall_score,
        report_data={
            "url": diagnostic.get("url"),
            "status": diagnostic.get("status"),
            "error": diagnostic.get("error"),
            "scores": diagnostic.get("scores") or {},
            "findings": findings,
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
        status=str(diagnostic.get("status") or "completed"),
        scores=AuditScoreSummary(
            overall=float(scores["overall"]),
            passed_count=int(scores["passed_count"]),
            total_count=int(scores["total_count"]),
        ),
        findings=[
            AuditFinding(
                pillar=str(f.get("pillar") or "General"),
                check=str(f.get("check") or "check"),
                passed=bool(f.get("passed")),
                details=f.get("details"),
            )
            for f in findings
        ],
        pillars=pillars,
        generated_at=lead.created_at,
    )

    return AuditRunResponse(
        status=str(diagnostic.get("status") or "completed"),
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

    findings: list[Dict[str, Any]] = lead.report_data.get("findings") or []
    pillars, scores = _build_pillars_from_findings(findings)
    overall_score = float(scores["overall"])

    return AuditReport(
        audit_id=lead.id,
        url=lead.target_url,
        business_name=lead.business_name,
        industry=lead.industry,
        overall_score=overall_score,
        status=str(lead.report_data.get("status") or "completed"),
        scores=AuditScoreSummary(
            overall=float(scores["overall"]),
            passed_count=int(scores["passed_count"]),
            total_count=int(scores["total_count"]),
        ),
        findings=[
            AuditFinding(
                pillar=str(f.get("pillar") or "General"),
                check=str(f.get("check") or "check"),
                passed=bool(f.get("passed")),
                details=f.get("details"),
            )
            for f in findings
        ],
        pillars=pillars,
        generated_at=lead.created_at,
    )
