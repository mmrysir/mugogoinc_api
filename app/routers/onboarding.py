from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.auth import get_current_user
from app.models.user import User, UserRole
from app.models.organization import Organization
from app.models.tenant_channels import ClientMarketingChannel, AgentOrgAssignment
from app.services.onboarding import analyze_and_seed_entity

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

class OnboardingRequest(BaseModel):
    org_id: str
    website_url: str
    customer_id: Optional[str] = None

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
        channel_record.gsc_platform_properties = props
    else:
        props = {}
        if payload.customer_id:
            props["google_ads_customer_id"] = payload.customer_id
            
        channel_record = ClientMarketingChannel(
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
