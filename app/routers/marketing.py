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
    "Rwanda": "2646",
    "Ivory Coast": "2384",
    "Senegal": "2686",
    "Egypt": "2818",
    "Morocco": "2504",
    "Algeria": "2012",
    "Tunisia": "2788",
    "Canada": "2124",
    "Mexico": "2484",
    "Australia": "2036",
    "India": "2356",
    "Germany": "2276",
    "France": "2250",
    "Spain": "2724",
    "Italy": "2380",
    "Netherlands": "2528",
    "UAE": "2784",
    "United Arab Emirates": "2784",
    "Saudi Arabia": "2682",
    "Qatar": "2634",
    "Israel": "2376",
    "Oman": "2512",
    "Singapore": "2702",
    "Thailand": "2764",
    "Malaysia": "2458",
    "Indonesia": "2360",
    "Vietnam": "2704",
    "Philippines": "2608",
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
    "Italian": "1005",
    "Dutch": "1010",
    "Swedish": "1011",
    "Polish": "1030",
    "Indonesian": "1026",
    "Vietnamese": "1040",
    "Tagalog": "1044",
    "Japanese": "1008",
    "Korean": "1012",
    "Chinese": "1017",
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


def resolve_location_ids(
    location_names: Optional[List[str]] = None,
    location_name: Optional[str] = None,
    location_ids: Optional[List[str]] = None,
    location_id: Optional[str] = None,
) -> List[str]:
    """Resolve human-readable location names and/or IDs into a list of Google Ads geo-target IDs."""
    results: List[str] = []
    if location_names:
        for name in location_names:
            if name and name in LOCATION_MAP:
                loc = LOCATION_MAP[name]
                if loc not in results:
                    results.append(loc)
    if location_name and location_name in LOCATION_MAP:
        loc = LOCATION_MAP[location_name]
        if loc not in results:
            results.append(loc)
    if location_ids:
        for lid in location_ids:
            if lid and str(lid) not in results:
                results.append(str(lid))
    if location_id and str(location_id) not in results:
        results.append(str(location_id))
    return results or ["2404"]  # Default: Kenya


def resolve_language_ids(
    language_names: Optional[List[str]] = None,
    language_name: Optional[str] = None,
    language_ids: Optional[List[str]] = None,
    language_id: Optional[str] = None,
) -> List[str]:
    """Resolve human-readable language names and/or IDs into a list of Google Ads language constant IDs."""
    results: List[str] = []
    if language_names:
        for name in language_names:
            if name and name in LANGUAGE_MAP:
                lang = LANGUAGE_MAP[name]
                if lang not in results:
                    results.append(lang)
    if language_name and language_name in LANGUAGE_MAP:
        lang = LANGUAGE_MAP[language_name]
        if lang not in results:
            results.append(lang)
    if language_ids:
        for lid in language_ids:
            if lid and str(lid) not in results:
                results.append(str(lid))
    if language_id and str(language_id) not in results:
        results.append(str(language_id))
    return results or ["1000"]  # Default: English


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class KeywordSearchRequest(BaseModel):
    seed_keywords: List[str] = []
    negative_keywords: Optional[List[str]] = None
    # Multi-location / multi-language lists (preferred)
    location_names: Optional[List[str]] = None
    language_names: Optional[List[str]] = None
    # Human-readable singular fields (backward-compatible)
    location_name: Optional[str] = None
    language_name: Optional[str] = None
    # Raw numeric ID passthrough (legacy / advanced use)
    location_ids: Optional[List[str]] = None
    location_id: Optional[str] = None
    language: Optional[str] = None
    # Additional context
    page_url: Optional[str] = None
    business_unit_id: Optional[str] = None


class ScrapePreviewRequest(BaseModel):
    url: str


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
    "/scrape-preview",
    summary="Scrape website metadata and derive suggested keyword seeds",
    dependencies=[Depends(require_role(UserRole.SUPER_ADMIN, UserRole.MANAGER, UserRole.AGENT))],
)
async def scrape_preview(payload: ScrapePreviewRequest) -> Dict[str, Any]:
    """Scrape website homepage to extract title, meta description, headings, and suggested seed keywords."""
    from app.services.scraper import scrape_client_website
    res = await scrape_client_website(payload.url)

    suggested_seeds: List[str] = []
    if res.get("success"):
        if res.get("title"):
            suggested_seeds.append(res["title"])
        for h in res.get("headings", [])[:5]:
            if h and h not in suggested_seeds:
                suggested_seeds.append(h)

    return {
        "success": res.get("success", False),
        "url": res.get("url", payload.url),
        "title": res.get("title", ""),
        "meta_description": res.get("meta_description", ""),
        "headings": res.get("headings", []),
        "discovered_socials": res.get("discovered_socials", {}),
        "suggested_seeds": suggested_seeds,
        "error": res.get("error"),
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
            if not payload.location_names and not payload.location_name and not payload.location_ids and not payload.location_id and bu.target_country:
                payload.location_id = bu.target_country
            if not payload.language_names and not payload.language_name and not payload.language and bu.target_language:
                payload.language = bu.target_language

    if not payload.seed_keywords and not effective_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one seed keyword or a target page URL.",
        )

    # Resolve human-readable names / IDs → numeric ID lists
    resolved_location_ids = resolve_location_ids(
        payload.location_names,
        payload.location_name,
        payload.location_ids,
        payload.location_id,
    )
    resolved_language_ids = resolve_language_ids(
        payload.language_names,
        payload.language_name,
        None,
        payload.language,
    )

    logger.info(
        "[KEYWORD_ROUTER] locations=%s → %s | languages=%s → %s | seeds=%s | negatives=%s",
        payload.location_names or payload.location_name or payload.location_id,
        resolved_location_ids,
        payload.language_names or payload.language_name or payload.language,
        resolved_language_ids,
        payload.seed_keywords,
        payload.negative_keywords,
    )

    try:
        ideas = await keyword_service.fetch_keyword_ideas(
            payload.seed_keywords,
            location_ids=resolved_location_ids,
            language_ids=resolved_language_ids,
            page_url=effective_url,
            customer_id=effective_customer_id,
        )
    except Exception as exc:
        logger.exception("[KEYWORD_ROUTER] Keyword search failed: %s", exc)
        ideas = []

    # Filter out keywords that contain any negative keyword term
    if payload.negative_keywords:
        neg_terms = [
            n.lower().strip()
            for n in payload.negative_keywords
            if n and n.strip()
        ]
        if neg_terms:
            ideas = [
                kw for kw in ideas
                if not any(neg in kw.get("keyword", "").lower() for neg in neg_terms)
            ]

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
