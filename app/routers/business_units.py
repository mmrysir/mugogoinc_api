from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.auth import get_current_user
from app.models.audit import AuditLead
from app.models.business_unit import BusinessUnit
from app.models.organization import Organization
from pydantic import BaseModel
from app.models.tenant_channels import AgentOrgAssignment
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
            (Organization.assigned_agent_id == current_user.id)
            | (Organization.id.in_(
                select(AgentOrgAssignment.org_id).where(AgentOrgAssignment.agent_id == current_user.id)
            ))
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

    import json as _json

    updates = body.model_dump(exclude_unset=True)
    gbp_changed = (
        "google_business_place_id" in updates and updates["google_business_place_id"] != bu.google_business_place_id
    ) or (
        "google_business_map_url" in updates and updates["google_business_map_url"] != bu.google_business_map_url
    )

    for field, value in updates.items():
        setattr(bu, field, value)

    if gbp_changed:
        bu.google_business_rating = None
        bu.google_business_review_count = None
        if bu.digital_footprint_metrics:
            try:
                footprint = (
                    _json.loads(bu.digital_footprint_metrics)
                    if isinstance(bu.digital_footprint_metrics, str)
                    else dict(bu.digital_footprint_metrics)
                )
                footprint.pop("google_business", None)
                bu.digital_footprint_metrics = _json.dumps(footprint) if footprint else None
            except Exception:
                bu.digital_footprint_metrics = None

    await db.commit()
    await db.refresh(bu)
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

    import json as _json

    updates = body.model_dump(exclude_unset=True)
    gbp_changed = (
        "google_business_place_id" in updates and updates["google_business_place_id"] != bu.google_business_place_id
    ) or (
        "google_business_map_url" in updates and updates["google_business_map_url"] != bu.google_business_map_url
    )

    for field, value in updates.items():
        setattr(bu, field, value)

    if gbp_changed:
        bu.google_business_rating = None
        bu.google_business_review_count = None
        if bu.digital_footprint_metrics:
            try:
                footprint = (
                    _json.loads(bu.digital_footprint_metrics)
                    if isinstance(bu.digital_footprint_metrics, str)
                    else dict(bu.digital_footprint_metrics)
                )
                footprint.pop("google_business", None)
                bu.digital_footprint_metrics = _json.dumps(footprint) if footprint else None
            except Exception:
                bu.digital_footprint_metrics = None

    await db.commit()
    await db.refresh(bu)
    return bu


# ── GBP Verification Flow ────────────────────────────────────────────────────


class GBPPreviewRequest(BaseModel):
    place_id: Optional[str] = None
    maps_url: Optional[str] = None


class GBPConfirmRequest(BaseModel):
    place_id: str
    maps_url: Optional[str] = None
    name: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None


@router.post("/{bu_id}/gbp/preview")
async def preview_google_business_profile(
    bu_id: str,
    payload: GBPPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Dry-run lookup for agent/admin verification. Fetches GBP details
    from a Place ID or Maps URL WITHOUT mutating the database.
    """
    from app.services.places import fetch_google_place_details

    clean_place_id = (payload.place_id or "").strip() or None
    clean_maps_url = (payload.maps_url or "").strip() or None

    if not clean_place_id and not clean_maps_url:
        raise HTTPException(
            status_code=400,
            detail="Provide either a Google Place ID or a Google Maps URL.",
        )

    place_data = await fetch_google_place_details(
        place_id=clean_place_id,
        maps_url=clean_maps_url,
    )
    if not place_data:
        raise HTTPException(
            status_code=404,
            detail="No Google Business Profile found for that Place ID or Maps URL.",
        )

    return {
        "place_id": place_data.get("place_id") or clean_place_id,
        "name": place_data.get("name"),
        "rating": place_data.get("rating"),
        "review_count": place_data.get("review_count"),
        "business_status": place_data.get("business_status"),
        "map_url": place_data.get("map_url") or clean_maps_url,
    }


@router.post("/{bu_id}/gbp/confirm")
async def confirm_google_business_profile(
    bu_id: str,
    payload: GBPConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Commits the agent/admin-verified GBP to the database and locks it so
    automated name-based discovery does not overwrite it on future audits.
    """
    import json as _json

    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    clean_place_id = payload.place_id.strip()
    if not clean_place_id:
        raise HTTPException(status_code=400, detail="A valid Place ID is required to confirm.")

    # Update core business unit fields
    bu.google_business_place_id = clean_place_id
    if payload.maps_url:
        bu.google_business_map_url = payload.maps_url.strip()
    if payload.rating is not None:
        bu.google_business_rating = payload.rating
    if payload.review_count is not None:
        bu.google_business_review_count = payload.review_count

    # Store verification lock in digital_footprint_metrics["gbp"]
    metrics = {}
    if bu.digital_footprint_metrics:
        try:
            metrics = (
                _json.loads(bu.digital_footprint_metrics)
                if isinstance(bu.digital_footprint_metrics, str)
                else dict(bu.digital_footprint_metrics)
            )
        except Exception:
            metrics = {}
    metrics["gbp"] = {
        "place_id": clean_place_id,
        "name": payload.name,
        "rating": payload.rating,
        "review_count": payload.review_count,
        "map_url": bu.google_business_map_url,
        "verified": True,
        "verified_by": str(current_user.id),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    bu.digital_footprint_metrics = metrics

    await db.commit()
    await db.refresh(bu)

    return {
        "status": "verified",
        "place_id": bu.google_business_place_id,
        "name": payload.name,
        "rating": bu.google_business_rating,
        "review_count": bu.google_business_review_count,
        "map_url": bu.google_business_map_url,
        "verified": True,
    }


# ── Social Media Verification Flow ───────────────────────────────────────────


class SocialPreviewRequest(BaseModel):
    platform: str
    url: str


class SocialConfirmRequest(BaseModel):
    platform: str
    url: str
    handle: Optional[str] = None
    followers: Optional[int] = None
    posts_count: Optional[int] = None


@router.post("/{bu_id}/social/preview")
async def preview_social_profile(
    bu_id: str,
    payload: SocialPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Dry-run lookup for a social profile. Fetches public metadata
    (name, bio, follower count) WITHOUT mutating the database.
    """
    from app.services.social_scraper import audit_social_profile

    platform = payload.platform.strip().lower()
    url = payload.url.strip()

    if not platform or not url:
        raise HTTPException(status_code=400, detail="Platform and URL are required.")

    result = await audit_social_profile(platform, url)
    return {
        "platform": platform,
        "url": url,
        "active": result.get("active", False),
        "followers": result.get("followers"),
        "description_snippet": result.get("description_snippet"),
        "source": result.get("source"),
        "status_code": result.get("status_code"),
        "error": result.get("error"),
    }


@router.post("/{bu_id}/social/confirm")
async def confirm_social_profile(
    bu_id: str,
    payload: SocialConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Commits the verified social profile to the database and locks it."""
    import json as _json
    from datetime import datetime, timezone

    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    platform = payload.platform.strip().lower()
    url = payload.url.strip()

    if not platform or not url:
        raise HTTPException(status_code=400, detail="Platform and URL are required.")

    # Parse existing social_profiles
    profiles = {}
    if bu.social_profiles:
        try:
            profiles = (
                _json.loads(bu.social_profiles)
                if isinstance(bu.social_profiles, str)
                else dict(bu.social_profiles)
            )
        except Exception:
            profiles = {}

    profiles[platform] = {
        "url": url,
        "handle": payload.handle,
        "verified": True,
        "followers": payload.followers,
        "posts_count": payload.posts_count,
        "last_audited": datetime.now(timezone.utc).isoformat(),
        "verified_by": str(current_user.id),
    }
    bu.social_profiles = profiles

    # Also update social_media_links for backward compat
    social_links = {}
    if bu.social_media_links:
        try:
            social_links = (
                _json.loads(bu.social_media_links)
                if isinstance(bu.social_media_links, str) and bu.social_media_links.strip().startswith("{")
                else {}
            )
        except Exception:
            social_links = {}
    social_links[platform] = url
    bu.social_media_links = _json.dumps(social_links)

    await db.commit()
    await db.refresh(bu)

    return {
        "status": "verified",
        "platform": platform,
        "url": url,
        "verified": True,
    }


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
    import re as _re

    place_id = bu.google_business_place_id
    if not place_id and bu.google_business_map_url:
        m = _re.search(r"(ChIJ[A-Za-z0-9_-]+)", bu.google_business_map_url)
        if m:
            place_id = m.group(1)

    if not place_id and not bu.google_business_map_url:
        raise HTTPException(
            status_code=400,
            detail="Business Unit does not have a Google Business Place ID or Maps URL configured. Please add one in profile settings.",
        )

    place_data = await fetch_google_place_details(place_id=place_id, maps_url=bu.google_business_map_url)
    if not place_data:
        raise HTTPException(
            status_code=502,
            detail="Failed to retrieve data from Google Places API. Verify Place ID and GOOGLE_PLACES_API_KEY in environment.",
        )

    # Always update place_id from resolved data (no non-destructive guard)
    if place_data.get("place_id"):
        bu.google_business_place_id = place_data["place_id"]

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

    # Update the latest audit's report_data so the audit report reflects synced GBP data
    try:
        from sqlalchemy import select as _select

        audit_result = await db.execute(
            _select(AuditLead)
            .where(AuditLead.business_unit_id == bu_id)
            .order_by(AuditLead.created_at.desc())
            .limit(1)
        )
        latest_audit = audit_result.scalar_one_or_none()
        if latest_audit and isinstance(latest_audit.report_data, dict):
            report = dict(latest_audit.report_data)
            if "metrics" in report and isinstance(report["metrics"], dict):
                report["metrics"]["google_rating"] = bu.google_business_rating
                report["metrics"]["google_reviews"] = bu.google_business_review_count
            if "gbp_profile" in report and isinstance(report["gbp_profile"], dict):
                report["gbp_profile"]["rating"] = bu.google_business_rating
                report["gbp_profile"]["review_count"] = bu.google_business_review_count
                report["gbp_profile"]["place_id"] = bu.google_business_place_id
            latest_audit.report_data = report
    except Exception:
        pass

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


# ── Competitive Scope Refresh ─────────────────────────────────────────────────


class ScopeRefreshRequest(BaseModel):
    search_radius_meters: int = 10000
    competitor_limit: int = 5
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@router.post("/{bu_id}/competitors/refresh")
async def refresh_competitive_intelligence(
    bu_id: str,
    payload: ScopeRefreshRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Re-scope and re-run competitive & semantic intelligence for a BU."""
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise HTTPException(status_code=404, detail="Business Unit not found.")

    # Persist updated scope settings
    bu.search_radius_meters = payload.search_radius_meters
    bu.competitor_limit = payload.competitor_limit
    if payload.latitude is not None:
        bu.latitude = payload.latitude
    if payload.longitude is not None:
        bu.longitude = payload.longitude

    from app.services.competitors import run_competitive_semantic_module

    # Debug: resolve lat/lng
    _debug_lat = bu.latitude
    _debug_lng = bu.longitude
    _debug_place = bu.google_business_place_id
    try:
        updated_metrics = await run_competitive_semantic_module(bu)
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Module error: {type(e).__name__}: {str(e)[:500]}")

    # Debug info
    updated_metrics["_debug"] = {
        "bu_city": bu.city,
        "bu_industry": bu.industry,
        "bu_lat": bu.latitude,
        "bu_lng": bu.longitude,
        "competitors_found": len(updated_metrics.get("competitor_matrix", [])),
    }
    # Merge into digital_footprint_metrics
    existing = bu.digital_footprint_metrics
    if isinstance(existing, str):
        try:
            existing = __import__("json").loads(existing)
        except Exception:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["competitive_intelligence"] = updated_metrics
    bu.digital_footprint_metrics = existing

    await db.commit()
    await db.refresh(bu)

    return updated_metrics


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
