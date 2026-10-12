from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_unit import BusinessUnit
from app.utils.social_extractor import extract_social_links

logger = logging.getLogger("mugogo.gsc_social")


def _resolve_target_url(bu: BusinessUnit) -> Optional[str]:
    """Resolve the best base URL to discover social profiles from."""
    if bu.primary_url:
        url = bu.primary_url.strip()
        return url if url.startswith("http") else f"https://{url}"

    if bu.google_search_console_site:
        gsc = bu.google_search_console_site.strip()
        if gsc.startswith("sc-domain:"):
            domain = gsc.replace("sc-domain:", "").strip()
            return f"https://{domain}"
        if gsc.startswith("http"):
            return gsc
        return f"https://{gsc}"

    return None


async def extract_site_metadata(url: str) -> tuple[List[str], Optional[float], Optional[int]]:
    """
    Fetches the verified landing page and collects candidate social URLs and
    local SEO structured data (ratings, review counts) from:
    1. Standard <a> tags (headers, footers, social icons).
    2. JSON-LD schema.org 'sameAs' profile arrays & 'aggregateRating'.
    3. OpenGraph / Twitter meta tags.
    """
    candidates: List[str] = []
    scraped_rating: Optional[float] = None
    scraped_reviews: Optional[int] = None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 MugogoEngine/1.0"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                logger.warning("[SOCIAL_DISCOVERY] Site %s returned status %d", url, resp.status_code)
                return [], None, None
            html_text = resp.text
    except Exception as exc:
        logger.warning("[SOCIAL_DISCOVERY] Failed to fetch %s: %s", url, exc)
        return [], None, None

    soup = BeautifulSoup(html_text, "html.parser")

    # 1. <a> anchor tags
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href:
            candidates.append(href)

    # 2. JSON-LD schema.org sameAs links & aggregateRating
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if not script.string:
                continue
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    same_as = item.get("sameAs")
                    if isinstance(same_as, list):
                        candidates.extend([str(u) for u in same_as if isinstance(u, str)])
                    elif isinstance(same_as, str):
                        candidates.append(same_as)

                    # Check for aggregate rating
                    agg_rating = item.get("aggregateRating")
                    if isinstance(agg_rating, dict):
                        try:
                            val = agg_rating.get("ratingValue")
                            if val:
                                scraped_rating = float(val)
                            cnt = agg_rating.get("reviewCount") or agg_rating.get("ratingCount")
                            if cnt:
                                scraped_reviews = int(cnt)
                        except (ValueError, TypeError):
                            pass
        except Exception:
            continue

    # 3. Meta tags
    for meta in soup.find_all("meta", content=True):
        content = meta["content"].strip()
        if content.startswith("http") and any(
            s in content for s in ["facebook", "instagram", "linkedin", "twitter", "x.com", "youtube", "tiktok", "maps"]
        ):
            candidates.append(content)

    return candidates, scraped_rating, scraped_reviews


async def discover_business_socials_from_gsc(
    business_unit_id: str,
    db: AsyncSession,
) -> Dict[str, str]:
    """
    Discovers social media profiles and local GBP storefront data for a Business Unit
    by analyzing the verified domain/property links and persisting them into
    `business_units.social_media_links`, `google_business_map_url`, and metrics.
    """
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == business_unit_id))
    bu = result.scalar_one_or_none()

    if not bu:
        raise ValueError(f"Business Unit '{business_unit_id}' not found.")

    target_url = _resolve_target_url(bu)
    if not target_url:
        raise ValueError("Neither primary_url nor google_search_console_site is configured for this Business Unit.")

    logger.info("[GSC_SOCIAL] Starting discovery for BU %s via %s", bu.id, target_url)

    # 1. Collect all candidate URLs and rating signals from the target property
    candidates, scraped_rating, scraped_reviews = await extract_site_metadata(target_url)

    # 2. Extract validated platform profiles using regex patterns
    socials = extract_social_links(candidates)

    logger.info("[GSC_SOCIAL] Discovered %d social profiles for BU %s: %s", len(socials), bu.id, list(socials.keys()))

    # 3. Handle GBP Map URL if extracted
    if "google_business" in socials and not bu.google_business_map_url:
        bu.google_business_map_url = socials["google_business"]

    # 4. Handle ratings if discovered
    if scraped_rating is not None and not bu.google_business_rating:
        bu.google_business_rating = scraped_rating
    if scraped_reviews is not None and not bu.google_business_review_count:
        bu.google_business_review_count = scraped_reviews

    # 5. Persist digital footprint metrics summary
    footprint = {}
    for platform, url in socials.items():
        footprint[platform] = {"status": "active", "url": url}
    if bu.google_business_rating or bu.google_business_map_url:
        footprint["google_business"] = {
            "status": "verified",
            "rating": bu.google_business_rating,
            "reviews": bu.google_business_review_count,
            "map_url": bu.google_business_map_url,
        }
    bu.digital_footprint_metrics = json.dumps(footprint)

    # 6. Persist into business_unit.social_media_links (JSON string)
    if socials:
        bu.social_media_links = json.dumps(socials)

    await db.commit()
    await db.refresh(bu)

    return socials
