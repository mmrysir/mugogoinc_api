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
from app.models.tenant_channels import AgentOrgAssignment
from app.models.user import User, UserRole
from app.routers.auth import get_current_user
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationUpdate,
)

router = APIRouter(prefix="/organizations", tags=["Organizations"], dependencies=[Depends(get_current_user)])


async def _can_access(user: User, org: Organization, db: AsyncSession) -> bool:
    if user.role == UserRole.SUPER_ADMIN:
        return True
    if user.role == UserRole.MANAGER:
        return org.created_by_id == user.id or org.assigned_agent_id == user.id
    # AGENT: legacy FK or junction
    if org.assigned_agent_id == user.id:
        return True
    junction = await db.scalar(
        select(AgentOrgAssignment).where(
            AgentOrgAssignment.agent_id == user.id,
            AgentOrgAssignment.org_id == org.id,
        )
    )
    return junction is not None


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
        q = q.where(
            (Organization.assigned_agent_id == current_user.id)
            | (Organization.id.in_(
                select(AgentOrgAssignment.org_id).where(AgentOrgAssignment.agent_id == current_user.id)
            ))
        )

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

    from app.schemas.post import slugify

    target_slug = (body.slug or slugify(body.name)).strip()
    if not target_slug:
        target_slug = f"org-{str(uuid.uuid4())[:8]}"
    existing = await db.scalar(select(Organization).where(Organization.slug == target_slug))
    if existing:
        target_slug = f"{target_slug}-{str(uuid.uuid4())[:6]}"

    org = Organization(
        id=str(uuid.uuid4()),
        name=body.name,
        slug=target_slug,
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
    if not await _can_access(current_user, org, db):
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
    if not await _can_access(current_user, org, db):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(org, field, value)

    await db.flush()
    return org


@router.patch("/{org_id}/assign-agent", response_model=OrganizationResponse)
async def assign_agent(
    org_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Organization:
    """Assign or unassign an approved agent to an organization."""
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Insufficient privileges")

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    agent_id: Optional[str] = body.get("agent_id")  # None means unassign

    if agent_id:
        agent_result = await db.execute(
            select(User).where(
                User.id == agent_id,
                User.role == UserRole.AGENT,
                User.is_approved == True,  # noqa: E712
                User.is_frozen == False,   # noqa: E712
            )
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise HTTPException(
                status_code=404,
                detail="Agent not found, not approved, or account is frozen",
            )

    org.assigned_agent_id = agent_id
    # Maintain many-to-many junction (Agent OS) for multi-client agent support
    if agent_id:
        existing_assignment = await db.scalar(
            select(AgentOrgAssignment).where(
                AgentOrgAssignment.agent_id == agent_id,
                AgentOrgAssignment.org_id == org_id,
            )
        )
        if not existing_assignment:
            db.add(AgentOrgAssignment(
                agent_id=agent_id,
                org_id=org_id,
                assigned_by_id=current_user.id,
                is_primary=(org.assigned_agent_id is None),
            ))
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
    if not await _can_access(current_user, org, db):
        raise HTTPException(status_code=403, detail="Insufficient privileges")
    await db.delete(org)
    return Response(status_code=204)
