from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import Lead, DeliveryStatus, SourceChannel
from app.models.organization import Organization
from app.models.business_unit import BusinessUnit
from app.models.tenant_channels import AgentOrgAssignment
from app.models.user import User, UserRole
from app.routers.auth import get_current_user

router = APIRouter(prefix="/stats", tags=["Stats"], dependencies=[Depends(get_current_user)])


async def _can_access_org(user: User, org: Organization, db: AsyncSession) -> bool:
    if user.role == UserRole.SUPER_ADMIN:
        return True
    if user.role == UserRole.MANAGER:
        return org.created_by_id == user.id or org.assigned_agent_id == user.id
    # AGENT: legacy FK or junction assignment
    if org.assigned_agent_id == user.id:
        return True
    junction = await db.scalar(
        select(AgentOrgAssignment).where(
            AgentOrgAssignment.agent_id == user.id,
            AgentOrgAssignment.org_id == org.id,
        )
    )
    return junction is not None


@router.get("/dashboard", response_model=Dict[str, Any])
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Aggregated KPI metrics scoped to the caller's permitted organizations."""

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    # ── Build the set of organizations this user may see ─────────────────────
    allowed_orgs = select(Organization.id)
    if current_user.role == UserRole.SUPER_ADMIN:
        pass  # all organizations
    elif current_user.role == UserRole.MANAGER:
        allowed_orgs = allowed_orgs.where(
            (Organization.created_by_id == current_user.id)
            | (Organization.assigned_agent_id == current_user.id)
        )
    else:  # AGENT
        agent_org_ids = select(AgentOrgAssignment.org_id).where(
            AgentOrgAssignment.agent_id == current_user.id
        )
        allowed_orgs = allowed_orgs.where(
            (Organization.assigned_agent_id == current_user.id)
            | (Organization.id.in_(agent_org_ids))
        )

    allowed_orgs_subq = allowed_orgs.subquery()
    allowed_bus_ids = select(BusinessUnit.id).where(
        BusinessUnit.organization_id.in_(allowed_orgs_subq.select())
    )
    allowed_bus_ids_subq = allowed_bus_ids.subquery()

    scoped_lead_filter = Lead.business_unit_id.in_(allowed_bus_ids_subq.select())

    # ── Total counts (scoped) ────────────────────────────────────────────────
    total_orgs = (
        await db.execute(
            select(func.count()).select_from(allowed_orgs_subq)
        )
    ).scalar_one()
    total_bus = (
        await db.execute(
            select(func.count(BusinessUnit.id)).where(
                BusinessUnit.organization_id.in_(allowed_orgs_subq.select())
            )
        )
    ).scalar_one()
    total_leads = (
        await db.execute(
            select(func.count(Lead.id)).where(scoped_lead_filter)
        )
    ).scalar_one()

    # Leads today
    leads_today = (
        await db.execute(
            select(func.count(Lead.id)).where(
                scoped_lead_filter, Lead.created_at >= today_start
            )
        )
    ).scalar_one()

    # Leads this week
    leads_week = (
        await db.execute(
            select(func.count(Lead.id)).where(
                scoped_lead_filter, Lead.created_at >= week_start
            )
        )
    ).scalar_one()

    # Delivery breakdown
    delivered = (
        await db.execute(
            select(func.count(Lead.id)).where(
                scoped_lead_filter,
                Lead.delivery_status == DeliveryStatus.DELIVERED,
            )
        )
    ).scalar_one()
    failed = (
        await db.execute(
            select(func.count(Lead.id)).where(
                scoped_lead_filter,
                Lead.delivery_status == DeliveryStatus.FAILED,
            )
        )
    ).scalar_one()
    pending = (
        await db.execute(
            select(func.count(Lead.id)).where(
                scoped_lead_filter,
                Lead.delivery_status == DeliveryStatus.PENDING,
            )
        )
    ).scalar_one()

    delivery_rate = round((delivered / total_leads * 100), 1) if total_leads else 0.0

    # Channel breakdown
    channel_rows = (
        await db.execute(
            select(Lead.source_channel, func.count(Lead.id))
            .where(scoped_lead_filter)
            .group_by(Lead.source_channel)
        )
    ).all()
    channels = {row[0].value: row[1] for row in channel_rows}

    # Recent 7-day daily volume
    daily_rows = (
        await db.execute(
            select(
                func.date(Lead.created_at).label("day"),
                func.count(Lead.id).label("count"),
            )
            .where(scoped_lead_filter, Lead.created_at >= week_start)
            .group_by(func.date(Lead.created_at))
            .order_by(func.date(Lead.created_at))
        )
    ).all()
    daily_volume = [{"date": str(row.day), "count": row.count} for row in daily_rows]

    return {
        "organizations": total_orgs,
        "business_units": total_bus,
        "leads": {
            "total": total_leads,
            "today": leads_today,
            "this_week": leads_week,
            "delivered": delivered,
            "failed": failed,
            "pending": pending,
            "delivery_rate_pct": delivery_rate,
        },
        "channels": channels,
        "daily_volume": daily_volume,
        "generated_at": now.isoformat(),
    }


@router.get("/organizations/{org_id}", response_model=Dict[str, Any])
async def organization_stats(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """KPI metrics scoped to a single organization."""
    org = await db.scalar(select(Organization).where(Organization.id == org_id))
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not await _can_access_org(current_user, org, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    business_unit_ids = select(BusinessUnit.id).where(
        BusinessUnit.organization_id == org_id
    )
    leads_in_org = select(Lead).where(Lead.business_unit_id.in_(business_unit_ids))

    total_bus = (
        await db.execute(
            select(func.count(BusinessUnit.id)).where(
                BusinessUnit.organization_id == org_id
            )
        )
    ).scalar_one()

    total_leads = (
        await db.execute(select(func.count()).select_from(leads_in_org.subquery()))
    ).scalar_one()

    leads_today = (
        await db.execute(
            select(func.count())
            .select_from(leads_in_org.where(Lead.created_at >= today_start).subquery())
        )
    ).scalar_one()

    leads_week = (
        await db.execute(
            select(func.count())
            .select_from(leads_in_org.where(Lead.created_at >= week_start).subquery())
        )
    ).scalar_one()

    delivered = (
        await db.execute(
            select(func.count())
            .select_from(
                leads_in_org.where(Lead.delivery_status == DeliveryStatus.DELIVERED).subquery()
            )
        )
    ).scalar_one()

    failed = (
        await db.execute(
            select(func.count())
            .select_from(
                leads_in_org.where(Lead.delivery_status == DeliveryStatus.FAILED).subquery()
            )
        )
    ).scalar_one()

    pending = (
        await db.execute(
            select(func.count())
            .select_from(
                leads_in_org.where(Lead.delivery_status == DeliveryStatus.PENDING).subquery()
            )
        )
    ).scalar_one()

    delivery_rate = round((delivered / total_leads * 100), 1) if total_leads else 0.0

    channel_rows = (
        await db.execute(
            select(Lead.source_channel, func.count(Lead.id))
            .where(Lead.business_unit_id.in_(business_unit_ids))
            .group_by(Lead.source_channel)
        )
    ).all()
    channels = {row[0].value: row[1] for row in channel_rows}

    daily_rows = (
        await db.execute(
            select(
                func.date(Lead.created_at).label("day"),
                func.count(Lead.id).label("count"),
            )
            .where(
                Lead.business_unit_id.in_(business_unit_ids),
                Lead.created_at >= week_start,
            )
            .group_by(func.date(Lead.created_at))
            .order_by(func.date(Lead.created_at))
        )
    ).all()
    daily_volume = [{"date": str(row.day), "count": row.count} for row in daily_rows]

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "business_units": total_bus,
        "leads": {
            "total": total_leads,
            "today": leads_today,
            "this_week": leads_week,
            "delivered": delivered,
            "failed": failed,
            "pending": pending,
            "delivery_rate_pct": delivery_rate,
        },
        "channels": channels,
        "daily_volume": daily_volume,
        "generated_at": now.isoformat(),
    }
