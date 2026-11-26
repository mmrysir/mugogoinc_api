"""Unified audit runner: orchestrates all channels atomically into digital_footprint_metrics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.competitors import run_competitive_semantic_module
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

    # 4. Run Competitive & Semantic Module (final pillar — includes competitor fetch + NLP)
    competitive_snapshot = await run_competitive_semantic_module(bu)
    competitors_data = competitive_snapshot.get("competitor_matrix", [])

    # 5. Compute Pillar Scores
    scores = calculate_digital_footprint_scores(
        gbp_data or {}, website_data, social_results, competitors_data
    )

    # Append competitive pillar score to the scores
    comp_score = (competitive_snapshot.get("pillar_summary") or {}).get("score", 0)
    scores["competitive_semantic_score"] = comp_score

    # Recalculate overall with competitive weight (15%)
    scores["overall_health_score"] = int(
        scores.get("local_authority_score", 0) * 0.30
        + scores.get("website_health_score", 0) * 0.20
        + scores.get("social_presence_score", 0) * 0.20
        + comp_score * 0.15
        + 15  # baseline
    )
    scores["overall_health_score"] = max(0, min(100, scores["overall_health_score"]))

    # 7. Commit Snapshot to JSONB
    audit_snapshot = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "scores": scores,
        "gbp": gbp_data,
        "website": website_data,
        "social": social_results,
        "competitors": competitors_data,
        "competitive_intelligence": competitive_snapshot,
    }

    bu.digital_footprint_metrics = audit_snapshot
    await db.commit()
    return audit_snapshot