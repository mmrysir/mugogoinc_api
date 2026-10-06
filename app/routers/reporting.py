"""
WhatsApp Reporting Router — Mugogo Engine.

Exposes endpoints for:
  POST /reports/weekly-dispatch    — Manually trigger a weekly report for one or all orgs.
  GET  /reports/weekly-preview     — Preview the report content without dispatching.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.auth import get_current_user
from app.models.user import User, UserRole
from app.models.organization import Organization
from app.services.reporting import build_weekly_summary, dispatch_weekly_report, format_weekly_report

router = APIRouter(prefix="/reports", tags=["Reporting"])


class WeeklyDispatchRequest(BaseModel):
    recipient_phone: str
    org_id: Optional[str] = None   # None → platform-wide report


def _require_manager_or_above(current_user: User) -> None:
    """Raise 403 if the user is a plain Agent."""
    if current_user.role == UserRole.AGENT:
        raise HTTPException(
            status_code=403,
            detail="Only Managers and Super Admins can dispatch reports."
        )


async def _resolve_org_name(db: AsyncSession, org_id: Optional[str]) -> str:
    if not org_id:
        return "All Organizations"
    org = await db.scalar(select(Organization).where(Organization.id == org_id))
    return org.name if org else org_id


@router.post("/weekly-dispatch")
async def dispatch_weekly(
    payload: WeeklyDispatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Trigger an on-demand weekly performance report dispatched via WhatsApp.
    Managers may only dispatch for organizations they own.
    Super Admins may dispatch platform-wide or for any org.
    """
    _require_manager_or_above(current_user)

    # Managers scoped to their own orgs
    if current_user.role == UserRole.MANAGER and payload.org_id:
        org = await db.scalar(
            select(Organization).where(
                Organization.id == payload.org_id,
                (Organization.created_by_id == current_user.id)
                | (Organization.assigned_agent_id == current_user.id),
            )
        )
        if not org:
            raise HTTPException(status_code=403, detail="You do not have access to this organization.")

    org_name = await _resolve_org_name(db, payload.org_id)

    result = await dispatch_weekly_report(
        db=db,
        recipient_phone=payload.recipient_phone,
        org_id=payload.org_id,
        org_name=org_name,
    )

    return {
        "status": "dispatched" if result["dispatched"] else "failed",
        "message": f"Weekly report {'sent' if result['dispatched'] else 'FAILED to send'} to {payload.recipient_phone}.",
        "data": result,
    }


@router.get("/weekly-preview")
async def preview_weekly(
    org_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Preview the weekly performance report body without dispatching it.
    Useful for verifying the report format before scheduling automated runs.
    """
    _require_manager_or_above(current_user)

    org_name = await _resolve_org_name(db, org_id)
    summary  = await build_weekly_summary(db, org_id=org_id)
    message  = format_weekly_report(summary, org_name=org_name)

    return {
        "status": "preview",
        "org_id": org_id,
        "org_name": org_name,
        "summary": summary,
        "message": message,
    }
