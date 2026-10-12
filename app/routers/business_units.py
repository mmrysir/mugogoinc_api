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
    current_user: User = Depends(get_current_user),
) -> BusinessUnitListResponse:
    q = select(BusinessUnit)

    if current_user.role == UserRole.SUPER_ADMIN:
        # Super admins see all business units
        if organization_id:
            q = q.where(BusinessUnit.organization_id == organization_id)
    elif current_user.role == UserRole.MANAGER:
        # Managers see BUs from orgs they created or are assigned to
        manager_org_ids = select(Organization.id).where(
            (Organization.created_by_id == current_user.id)
            | (Organization.assigned_agent_id == current_user.id)
        )
        q = q.where(BusinessUnit.organization_id.in_(manager_org_ids))
        if organization_id:
            q = q.where(BusinessUnit.organization_id == organization_id)
    else:
        # Agents only see BUs from their assigned organizations
        agent_org_ids = select(Organization.id).where(
            Organization.assigned_agent_id == current_user.id
        )
        q = q.where(BusinessUnit.organization_id.in_(agent_org_ids))
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


@router.put("/{bu_id}/profile", response_model=BusinessUnitResponse)
async def update_business_unit_profile(
    bu_id: str,
    body: BusinessUnitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BusinessUnit:
    """Update onboarding profile metadata (primary_url, social_media_links, google_ads_customer_id, target_country, target_language)."""
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges to edit business unit profiles",
        )
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(bu, field, value)

    await db.flush()
    return bu


@router.post("/{bu_id}/discover-socials")
async def trigger_gsc_social_discovery(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Discovers social media profiles for a Business Unit by analyzing the verified
    domain/property links, saving them directly to the BU profile.
    """
    from app.services.gsc_social import discover_business_socials_from_gsc

    try:
        socials = await discover_business_socials_from_gsc(bu_id, db)
        # Fetch updated BU
        result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
        bu = result.scalar_one_or_none()
        return {
            "status": "success",
            "business_unit_id": bu_id,
            "social_media_links": socials,
            "google_business_rating": bu.google_business_rating if bu else None,
            "google_business_review_count": bu.google_business_review_count if bu else None,
            "google_business_map_url": bu.google_business_map_url if bu else None,
            "digital_footprint_metrics": bu.digital_footprint_metrics if bu else None,
            "raw_social_links_str": bu.social_media_links if bu else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Social profile discovery failed: {str(e)}"
        )


@router.post("/{bu_id}/sync-gbp")
async def sync_google_business_profile(
    bu_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Synchronizes live Google Business Profile reviews, ratings, and map metadata
    using Google Places API with the stored google_business_place_id.
    """
    import json
    from app.services.places import fetch_google_place_details

    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")
    if not bu.google_business_place_id:
        raise HTTPException(
            status_code=400,
            detail="Business Unit does not have a Google Business Place ID configured. Please add one in profile settings.",
        )

    place_data = await fetch_google_place_details(bu.google_business_place_id)
    if not place_data:
        raise HTTPException(
            status_code=502,
            detail="Failed to retrieve data from Google Places API. Verify Place ID and GOOGLE_PLACES_API_KEY in environment.",
        )

    if place_data.get("rating") is not None:
        bu.google_business_rating = place_data["rating"]
    if place_data.get("review_count") is not None:
        bu.google_business_review_count = place_data["review_count"]
    if place_data.get("map_url"):
        bu.google_business_map_url = place_data["map_url"]

    # Update digital footprint
    footprint = {}
    if bu.digital_footprint_metrics:
        try:
            footprint = (
                json.loads(bu.digital_footprint_metrics)
                if isinstance(bu.digital_footprint_metrics, str)
                else dict(bu.digital_footprint_metrics)
            )
        except Exception:
            footprint = {}
    footprint["google_business"] = {
        "status": "verified",
        "place_id": bu.google_business_place_id,
        "rating": bu.google_business_rating,
        "reviews": bu.google_business_review_count,
        "business_status": place_data.get("business_status"),
        "map_url": bu.google_business_map_url,
    }
    bu.digital_footprint_metrics = json.dumps(footprint)

    await db.commit()
    await db.refresh(bu)

    return {
        "status": "success",
        "data": place_data,
        "business_unit": {
            "id": bu.id,
            "google_business_place_id": bu.google_business_place_id,
            "google_business_rating": bu.google_business_rating,
            "google_business_review_count": bu.google_business_review_count,
            "google_business_map_url": bu.google_business_map_url,
            "digital_footprint_metrics": footprint,
        },
    }


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
