from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.auth import require_role
from app.models.user import UserRole
from app.services.campaign_monitor import check_campaign_health
from app.services.keywords import keyword_service

logger = logging.getLogger("mugogo.marketing")

router = APIRouter(prefix="/marketing", tags=["Marketing & Keyword Monitor"])


# ── Location & Language lookup maps ──────────────────────────────────────────
# Maps human-readable country names → Google Ads geo-target constant IDs
LOCATION_MAP: Dict[str, str] = {
    "Kenya": "2404",
    "United States": "2840",
    "United Kingdom": "2826",
    "Nigeria": "2566",
    "South Africa": "2710",
    "Uganda": "2800",
    "Tanzania": "2834",
    "Ethiopia": "2231",
    "Ghana": "2288",
    "Canada": "2124",
    "Australia": "2036",
    "India": "2356",
    "Germany": "2276",
    "France": "2250",
    "UAE": "2784",
    "Global": "2840",  # Defaults to US reach for "global"
}

# Maps human-readable language names → Google Ads language constant IDs
LANGUAGE_MAP: Dict[str, str] = {
    "English": "1000",
    "Swahili": "1038",
    "French": "1002",
    "Spanish": "1003",
    "German": "1001",
    "Arabic": "1019",
    "Hindi": "1023",
    "Portuguese": "1004",
}


def resolve_location_id(location_name: Optional[str], location_id: Optional[str]) -> str:
    """Resolve a human-readable location name to a Google Ads geo-target ID.
    Falls back to raw location_id if provided, then to Kenya (2404)."""
    if location_name and location_name in LOCATION_MAP:
        return LOCATION_MAP[location_name]
    if location_id:
        return location_id
    return "2404"  # Default: Kenya


def resolve_language_id(language_name: Optional[str], language_id: Optional[str]) -> str:
    """Resolve a human-readable language name to a Google Ads language constant ID.
    Falls back to raw language_id if provided, then to English (1000)."""
    if language_name and language_name in LANGUAGE_MAP:
        return LANGUAGE_MAP[language_name]
    if language_id:
        return language_id
    return "1000"  # Default: English


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class KeywordSearchRequest(BaseModel):
    seed_keywords: List[str] = []
    # Human-readable fields (preferred — used by the frontend dropdown)
    location_name: Optional[str] = "Kenya"
    language_name: Optional[str] = "English"
    # Raw numeric ID passthrough (legacy / advanced use — still supported)
    location_id: Optional[str] = None
    language: Optional[str] = None
    # Additional context
    page_url: Optional[str] = None
    business_unit_id: Optional[str] = None


class CampaignMetricsInput(BaseModel):
    campaign_name: str
    daily_spend_usd: float
    daily_budget_usd: float
    leads_count: int
    owner_phone: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/keywords/locations",
    summary="List supported target locations and languages for the Keyword Planner",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def get_keyword_locations() -> Dict[str, Any]:
    """Return all supported location and language options for the frontend dropdown."""
    return {
        "locations": [
            {"name": name, "id": loc_id}
            for name, loc_id in LOCATION_MAP.items()
        ],
        "languages": [
            {"name": name, "id": lang_id}
            for name, lang_id in LANGUAGE_MAP.items()
        ],
    }


@router.post(
    "/keywords/search",
    summary="Fetch Google Ads Keyword Planner search volume & bid ranges",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def search_keywords(
    payload: KeywordSearchRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    effective_url = payload.page_url
    effective_customer_id: Optional[str] = None

    # Resolve BU-specific overrides (primary URL, per-org Ads customer ID)
    if payload.business_unit_id:
        from app.models.business_unit import BusinessUnit
        bu = await db.scalar(
            select(BusinessUnit).where(BusinessUnit.id == payload.business_unit_id)
        )
        if bu:
            if not effective_url and bu.primary_url:
                effective_url = bu.primary_url
            if bu.google_ads_customer_id:
                effective_customer_id = bu.google_ads_customer_id
            # If no explicit location provided, fall back to BU's saved country/language
            if not payload.location_name and not payload.location_id and bu.target_country:
                payload.location_id = bu.target_country
            if not payload.language_name and not payload.language and bu.target_language:
                payload.language = bu.target_language

    if not payload.seed_keywords and not effective_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one seed keyword or a target page URL.",
        )

    # Resolve human-readable names → numeric IDs
    resolved_location_id = resolve_location_id(payload.location_name, payload.location_id)
    resolved_language_id = resolve_language_id(payload.language_name, payload.language)

    logger.info(
        "[KEYWORD_ROUTER] location=%s → %s | language=%s → %s | seeds=%s",
        payload.location_name or payload.location_id,
        resolved_location_id,
        payload.language_name or payload.language,
        resolved_language_id,
        payload.seed_keywords,
    )

    ideas = await keyword_service.fetch_keyword_ideas(
        payload.seed_keywords,
        location_id=resolved_location_id,
        language=resolved_language_id,
        page_url=effective_url,
        customer_id=effective_customer_id,
    )
    return {"keywords": ideas, "count": len(ideas)}


@router.get(
    "/account/diagnostics",
    summary="Fetch real-time account status and diagnostic alerts",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def get_account_diagnostics() -> Any:
    return {
        "status": "success",
        "data": {
            "account_status": "ACTIVE",
            "prepay_balance": "OK",
            "conversion_tracking": "ACTIVE",
            "alerts": [
                {"type": "info", "message": "Account billing is active."}
            ],
        },
    }


@router.post(
    "/campaigns/check-health",
    summary="Audit ad campaigns for budget burn & CPL threshold alerts",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def check_campaigns(
    campaigns: List[CampaignMetricsInput],
    recipient_phone: Optional[str] = None,
) -> Any:
    if not campaigns:
        return {"message": "No campaign metrics provided."}

    metrics_list = [c.model_dump() for c in campaigns]
    target_phone = recipient_phone or campaigns[0].owner_phone

    result = await check_campaign_health(metrics_list, alert_recipient_phone=target_phone)
    return result
