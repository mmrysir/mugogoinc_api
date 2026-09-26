from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from app.routers.auth import get_current_user
from app.models.user import User, UserRole
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.routers.auth import get_current_user
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationUpdate,
)

router = APIRouter(prefix="/organizations", tags=["Organizations"], dependencies=[Depends(get_current_user)])


def _can_access(user: User, org: Organization) -> bool:
    if user.role == UserRole.SUPER_ADMIN:
        return True
    if user.role == UserRole.MANAGER:
        return org.created_by_id == user.id or org.assigned_agent_id == user.id
    return org.assigned_agent_id == user.id


@router.get("", response_model=OrganizationListResponse)
async def list_organizations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrganizationListResponse:
    q = select(Organization)

    if current_user.role == UserRole.SUPER_ADMIN:
        # Super admins see every organization.
        pass
    elif current_user.role == UserRole.MANAGER:
        q = q.where(
            (Organization.created_by_id == current_user.id)
            | (Organization.assigned_agent_id == current_user.id)
        )
    else:  # AGENT
        q = q.where(Organization.assigned_agent_id == current_user.id)

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()

    result = await db.execute(
        q.order_by(Organization.created_at.desc()).offset(skip).limit(limit)
    )
    items = result.scalars().all()
    return OrganizationListResponse(total=total, items=list(items))


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Organization:
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges to create organizations",
        )

    org = Organization(
        id=str(uuid.uuid4()),
        name=body.name,
        status=body.status,
        assigned_agent_id=body.assigned_agent_id,
        created_by_id=current_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(org)
    await db.flush()
    return org


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Organization:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not _can_access(current_user, org):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    return org


@router.patch("/{org_id}", response_model=OrganizationResponse)
async def update_organization(
    org_id: str,
    body: OrganizationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Organization:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    if not _can_access(current_user, org):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(org, field, value)

    await db.flush()
    return org


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    # Ensure only SUPER_ADMIN can delete organizations
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    if not _can_access(current_user, org):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    await db.delete(org)
    return Response(status_code=204)
