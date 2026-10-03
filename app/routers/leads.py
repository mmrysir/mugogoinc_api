from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from app.routers.auth import get_current_user
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import Lead, DeliveryStatus, SourceChannel
from app.schemas.lead import LeadListResponse, LeadResponse

router = APIRouter(prefix="/leads", tags=["Leads"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=LeadListResponse)
async def list_leads(
    business_unit_id: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    delivery_status: Optional[DeliveryStatus] = Query(None),
    source_channel: Optional[SourceChannel] = Query(None),
    date_from: Optional[datetime] = Query(None, description="ISO 8601 datetime – filter leads created at or after this time"),
    date_to: Optional[datetime] = Query(None, description="ISO 8601 datetime – filter leads created before or at this time"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> LeadListResponse:
    from sqlalchemy import func
    from app.models.business_unit import BusinessUnit

    q = select(Lead)

    if business_unit_id:
        q = q.where(Lead.business_unit_id == business_unit_id)

    if organization_id:
        q = q.join(BusinessUnit, Lead.business_unit_id == BusinessUnit.id).where(
            BusinessUnit.organization_id == organization_id
        )

    if delivery_status:
        q = q.where(Lead.delivery_status == delivery_status)

    if source_channel:
        q = q.where(Lead.source_channel == source_channel)

    if date_from:
        q = q.where(Lead.created_at >= date_from)

    if date_to:
        q = q.where(Lead.created_at <= date_to)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        q.order_by(Lead.created_at.desc()).offset(skip).limit(limit)
    )
    items = result.scalars().all()
    return LeadListResponse(total=total, items=list(items))


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
) -> Lead:
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead
