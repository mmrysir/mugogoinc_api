from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.auth import get_current_user
from app.models.business_unit import BusinessUnit
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.business_unit import (
    BusinessUnitCreate,
    BusinessUnitListResponse,
    BusinessUnitResponse,
    BusinessUnitUpdate,
)

router = APIRouter(
    prefix="/business-units",
    tags=["Business Units"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=BusinessUnitListResponse)
async def list_business_units(
    organization_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> BusinessUnitListResponse:
    q = select(BusinessUnit)
    if organization_id:
        q = q.where(BusinessUnit.organization_id == organization_id)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        q.order_by(BusinessUnit.created_at.desc()).offset(skip).limit(limit)
    )
    items = result.scalars().all()
    return BusinessUnitListResponse(total=total, items=list(items))


@router.post("", response_model=BusinessUnitResponse, status_code=status.HTTP_201_CREATED)
async def create_business_unit(
    body: BusinessUnitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessUnit:
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges to create business units",
        )
    # Validate parent org exists
    org_result = await db.execute(
        select(Organization).where(Organization.id == body.organization_id)
    )
    if not org_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Organization not found")

    bu = BusinessUnit(
        id=str(uuid.uuid4()),
        organization_id=body.organization_id,
        name=body.name,
        route_type=body.route_type,
        webhook_url=body.webhook_url,
        owner_whatsapp_number=body.owner_whatsapp_number,
        owner_email=body.owner_email,
        created_at=datetime.now(timezone.utc),
    )
    db.add(bu)
    await db.flush()
    return bu


@router.get("/{bu_id}", response_model=BusinessUnitResponse)
async def get_business_unit(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
) -> BusinessUnit:
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found")
    return bu


@router.patch("/{bu_id}", response_model=BusinessUnitResponse)
async def update_business_unit(
    bu_id: str,
    body: BusinessUnitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessUnit:
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges to edit business units",
        )
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(bu, field, value)

    await db.flush()
    return bu


@router.delete("/{bu_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business_unit(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Super Admins can delete business units",
        )
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found")
    await db.delete(bu)
    return Response(status_code=204)
