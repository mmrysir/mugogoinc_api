from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.lead import DeliveryStatus, Lead, SourceChannel
from app.models.user import User, UserRole
from app.routers.auth import get_current_user
from app.schemas.lead import LeadListResponse, LeadResponse
from app.utils.privacy import mask_email, mask_name, mask_phone

router = APIRouter(prefix="/leads", tags=["Leads"], dependencies=[Depends(get_current_user)])


def _serialize_lead(lead: Lead, can_view_pii: bool) -> LeadResponse:
    resp = LeadResponse.model_validate(lead)
    if not can_view_pii:
        resp.is_blurred = True
        resp.full_name = mask_name(lead.full_name)
        resp.phone_number = mask_phone(lead.phone_number)
        resp.email = mask_email(lead.email)
        # Mask IP address — personal data under GDPR / data minimisation
        if resp.ip_address:
            resp.ip_address = "●●●.●●●.●●●.●●●"
    return resp


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
    current_user: User = Depends(get_current_user),
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
        q.order_by(Lead.last_activity_at.desc()).offset(skip).limit(limit)
    )
    items = result.scalars().all()

    can_view_pii = (
        current_user.role in [UserRole.SUPER_ADMIN, UserRole.MANAGER] or
        (current_user.is_approved and not current_user.is_frozen)
    )

    lead_responses = [_serialize_lead(lead, can_view_pii) for lead in items]
    return LeadListResponse(total=total, items=lead_responses)


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LeadResponse:
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    can_view_pii = (
        current_user.role in [UserRole.SUPER_ADMIN, UserRole.MANAGER] or
        (current_user.is_approved and not current_user.is_frozen)
    )
    return _serialize_lead(lead, can_view_pii)

