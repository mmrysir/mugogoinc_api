"""Unified audit runner: orchestrates all channels atomically into digital_footprint_metrics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.competitors import benchmark_local_competitors
from app.services.places import resolve_and_persist_business_unit_gbp
from app.services.scoring import calculate_digital_footprint_scores
from app.services.social_scraper import audit_social_profile


async def run_comprehensive_agency_audit(bu: Any, db: AsyncSession) -> dict:
    """Orchestrate all channels atomically into digital_footprint_metrics."""
    # 1. Resolve GBP (respecting locked verified state)
    gbp_data = await resolve_and_persist_business_unit_gbp(bu)

    # 2. Audit Social Channels (prefer verified social_profiles)
    social_links: Dict[str, str] = {}
    social_profiles = getattr(bu, "social_profiles", None)
    if isinstance(social_profiles, str):
        try:
            social_profiles = json.loads(social_profiles)
        except Exception:
            social_profiles = {}
    if isinstance(social_profiles, dict):
        for platform, info in social_profiles.items():
            if isinstance(info, dict) and info.get("url"):
                social_links[platform] = info["url"]
    # Fallback to social_media_links if no verified profiles
    if not social_links:
        raw = getattr(bu, "social_media_links", None)
        if isinstance(raw, str):
            try:
                social_links = json.loads(raw)
            except Exception:
                social_links = {}
        elif isinstance(raw, dict):
            social_links = raw

    social_results = {
        "instagram": await audit_social_profile("instagram", social_links.get("instagram")),
        "facebook": await audit_social_profile("facebook", social_links.get("facebook")),
        "linkedin": await audit_social_profile("linkedin", social_links.get("linkedin")),
        "tiktok": await audit_social_profile("tiktok", social_links.get("tiktok")),
    }

    # 3. Basic Website Health Check Stub
    website_url = getattr(bu, "website_url", None) or getattr(bu, "primary_url", None)
    website_data = {
        "url": website_url,
        "ssl": str(website_url or "").startswith("https"),
        "status_code": 200,
        "has_meta_description": True,
    }

    # 4. Benchmark Competitors
    competitors_data = await benchmark_local_competitors(
        industry=getattr(bu, "industry", "business") or "business",
        city=getattr(bu, "city", "") or "",
        radius_meters=getattr(bu, "search_radius_meters", 5000) or 5000,
        scope_type=getattr(bu, "geographic_scope_type", "radius") or "radius",
    )

    # 5. Compute Pillar Scores
    scores = calculate_digital_footprint_scores(
        gbp_data or {}, website_data, social_results, competitors_data
    )

    # 6. Commit Snapshot to JSONB
    audit_snapshot = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "scores": scores,
        "gbp": gbp_data,
        "website": website_data,
        "social": social_results,
        "competitors": competitors_data,
    }

    bu.digital_footprint_metrics = audit_snapshot
    await db.commit()
    return audit_snapshot