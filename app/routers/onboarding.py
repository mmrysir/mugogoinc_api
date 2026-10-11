from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.auth import get_current_user
from app.models.user import User, UserRole
from app.models.organization import Organization, OrgStatus
from app.models.business_unit import BusinessUnit, RouteType
from app.models.tenant_channels import ClientMarketingChannel, AgentOrgAssignment
from app.services.onboarding import analyze_and_seed_entity
from app.schemas.post import slugify
from app.schemas.onboarding import OnboardingSetupRequest, OnboardingSetupResponse

logger = logging.getLogger("mugogo.onboarding")

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

class OnboardingRequest(BaseModel):
    org_id: str
    website_url: str
    customer_id: Optional[str] = None


@router.post("/setup", response_model=OnboardingSetupResponse, status_code=status.HTTP_201_CREATED)
async def setup_onboarding_tenant(
    payload: OnboardingSetupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Atomic Tenant Onboarding:
    1. Validates operator role (SUPER_ADMIN or MANAGER).
    2. Validates slug uniqueness.
    3. Atomically provisions Organization + BusinessUnit + ClientMarketingChannel.
    4. Runs website intelligence & keyword analysis with graceful error isolation.
    """
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Managers and Super Admins can onboard new client organizations.",
        )

    # ── Slug Generation & Validation ──
    raw_slug = payload.org_slug or slugify(payload.org_name)
    target_slug = (raw_slug or f"org-{str(uuid.uuid4())[:8]}").strip().lower()

    existing_org = await db.scalar(
        select(Organization).where(Organization.slug == target_slug)
    )
    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization slug '{target_slug}' is already taken. Please provide a unique slug.",
        )

    # ── Derive Google Search Console Site if omitted ──
    clean_domain = (
        payload.website_url.replace("https://", "")
        .replace("http://", "")
        .split("/")[0]
        .replace("www.", "")
    )
    gsc_property = payload.google_search_console_site or f"sc-domain:{clean_domain}"

    # ── 1. Create Organization ──
    org_id = str(uuid.uuid4())
    org = Organization(
        id=org_id,
        name=payload.org_name.strip(),
        slug=target_slug,
        status=OrgStatus.ACTIVE,
        created_by_id=current_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(org)

    # ── 2. Create Default Business Unit ──
    bu_id = str(uuid.uuid4())
    bu_name = payload.bu_name.strip() if payload.bu_name else f"{payload.org_name.strip()} - Main"
    bu = BusinessUnit(
        id=bu_id,
        organization_id=org_id,
        name=bu_name,
        route_type=RouteType.EXPRESS,
        owner_whatsapp_number=payload.owner_whatsapp_number,
        owner_email=payload.owner_email,
        primary_url=payload.website_url,
        google_ads_customer_id=payload.google_ads_customer_id,
        google_search_console_site=gsc_property,
        social_media_links=payload.social_media_links,
        target_country=payload.target_country or "2404",
        target_language=payload.target_language or "1000",
        created_at=datetime.now(timezone.utc),
    )
    db.add(bu)

    # ── 3. Create Client Marketing Channel ──
    initial_props = {}
    if payload.google_ads_customer_id:
        initial_props["google_ads_customer_id"] = payload.google_ads_customer_id

    channel = ClientMarketingChannel(
        id=str(uuid.uuid4()),
        org_id=org_id,
        website_url=payload.website_url,
        gsc_domain_property=gsc_property,
        gsc_platform_properties=initial_props,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(channel)

    # Commit DB transaction atomically
    await db.commit()
    logger.info("[ONBOARDING_SETUP] Provisioned Org=%s (%s), BU=%s", org.name, org_id, bu_id)

    # ── 4. Run Website & Keyword Intelligence (Graceful Fallback) ──
    analysis_data = None
    try:
        analysis_data = await analyze_and_seed_entity(
            org_id=org_id,
            website_url=payload.website_url,
            customer_id=payload.google_ads_customer_id,
        )
        discovered_socials = (
            analysis_data.get("social_insights", {}).get("discovered_socials", {})
        )
        if discovered_socials:
            props = dict(channel.gsc_platform_properties or {})
            props.update(discovered_socials)
            channel.gsc_platform_properties = props

            if not bu.social_media_links:
                bu.social_media_links = "\n".join(
                    f"{plat}: {u}" for plat, u in discovered_socials.items()
                )
            await db.commit()
    except Exception as exc:
        logger.warning(
            "[ONBOARDING_SETUP] Live scan encountered an error for org %s: %s",
            org_id,
            exc,
        )
        analysis_data = {
            "org_id": org_id,
            "website_analysis": {
                "success": False,
                "url": payload.website_url,
                "error": str(exc),
            },
            "seed_keywords_used": [],
            "keyword_opportunities_count": 0,
            "top_keywords": [],
            "social_insights": {"discovered_socials": {}},
        }

    return {
        "status": "success",
        "message": f"Successfully onboarded '{payload.org_name}' and provisioned default business unit.",
        "org_id": org_id,
        "org_slug": target_slug,
        "bu_id": bu_id,
        "data": analysis_data,
    }


@router.post("/analyze-entity")
async def analyze_entity_endpoint(
    payload: OnboardingRequest, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # ── RBAC Ownership Check ──
    if current_user.role == UserRole.AGENT:
        if not current_user.is_approved or current_user.is_frozen:
            raise HTTPException(status_code=403, detail="Unapproved or frozen agents cannot perform onboarding analysis.")
        
        # Check AgentOrgAssignment
        junction_check = await db.scalar(
            select(AgentOrgAssignment).where(
                AgentOrgAssignment.agent_id == current_user.id,
                AgentOrgAssignment.org_id == payload.org_id,
            )
        )
        if not junction_check:
            # Fallback legacy check
            legacy_check = await db.scalar(
                select(Organization).where(
                    Organization.id == payload.org_id,
                    Organization.assigned_agent_id == current_user.id,
                )
            )
            if not legacy_check:
                raise HTTPException(status_code=403, detail="You do not have access to this organization.")
                
    elif current_user.role == UserRole.MANAGER:
        # Check Manager ownership
        org = await db.scalar(
            select(Organization).where(
                Organization.id == payload.org_id,
                (Organization.created_by_id == current_user.id) |
                (Organization.assigned_agent_id == current_user.id),
            )
        )
        if not org:
            raise HTTPException(status_code=403, detail="You do not have access to this organization.")

    # Run scraper + keyword analyzer
    result = await analyze_and_seed_entity(
        org_id=payload.org_id,
        website_url=payload.website_url,
        customer_id=payload.customer_id
    )
    
    # Save or update client marketing channel record with the website URL
    stmt = select(ClientMarketingChannel).where(ClientMarketingChannel.org_id == payload.org_id)
    channel_record = (await db.execute(stmt)).scalar_one_or_none()
    
    if channel_record:
        channel_record.website_url = payload.website_url
        props = channel_record.gsc_platform_properties or {}
        if payload.customer_id:
            props["google_ads_customer_id"] = payload.customer_id
        if result.get("social_insights", {}).get("discovered_socials"):
            props.update(result["social_insights"]["discovered_socials"])
        channel_record.gsc_platform_properties = props
    else:
        props = {}
        if payload.customer_id:
            props["google_ads_customer_id"] = payload.customer_id
        if result.get("social_insights", {}).get("discovered_socials"):
            props.update(result["social_insights"]["discovered_socials"])
            
        channel_record = ClientMarketingChannel(
            id=str(uuid.uuid4()),
            org_id=payload.org_id,
            website_url=payload.website_url,
            gsc_platform_properties=props
        )
        db.add(channel_record)
        
    await db.commit()
    
    return {
        "status": "success",
        "message": "Entity analyzed successfully and marketing configuration saved.",
        "data": result
    }
