from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.organization import Organization, OrgStatus


async def get_current_tenant(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_mugogo_key: Optional[str] = Header(None, alias="X-Mugogo-Api-Key"),
    db: AsyncSession = Depends(get_db),
) -> Optional[Organization]:
    """
    Tenant Resolver Dependency:
    Extracts tenant API key from request header ('X-API-Key' or 'X-Mugogo-Api-Key'),
    validates the key against the database, and resolves the exact client organization tenant.
    """
    api_key = x_api_key or x_mugogo_key
    if not api_key:
        return None

    query = select(Organization).where(Organization.api_key == api_key)
    result = await db.execute(query)
    tenant = result.scalar_one_or_none()

    if not tenant or tenant.status == OrgStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or deactivated Organization API Key.",
        )

    return tenant


async def require_tenant(
    tenant: Optional[Organization] = Depends(get_current_tenant),
) -> Organization:
    """Strict dependency requiring a valid tenant API key."""
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required X-API-Key header.",
        )
    return tenant
