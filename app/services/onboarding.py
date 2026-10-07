"""
onboarding.py — Entity Analysis & Keyword Seeding Service

Workflow:
  1. Scrape the client's website (title, meta description, h1/h2 headings)
     for SEO context — this is a legitimate metadata fetch, not social scraping.
  2. Build automated seed keywords from the scraped site context.
  3. Fetch live keyword volume data via the Google Ads Keyword Planner API.

NOTE: Automatic social media discovery has been removed (D3 decision, 2026-10-07).
Social links are entered manually via the Business Unit profile form and stored
in BusinessUnit.social_media_links.
"""
import logging
from typing import Optional

from app.services.scraper import scrape_client_website
from app.services.keywords import keyword_service

logger = logging.getLogger("mugogo.onboarding")


async def analyze_and_seed_entity(
    org_id: str,
    website_url: str,
    customer_id: Optional[str] = None,
) -> dict:
    """
    Analyze a client's website and seed keyword opportunities.

    Returns a dict with:
      - website_analysis: scraped title, meta description, headings
      - seed_keywords_used: keywords fed into the Planner
      - keyword_opportunities_count: total ideas returned
      - top_keywords: top 10 preview rows
    """
    # Step 1: Scrape website metadata (title, description, headings)
    scrape_result = await scrape_client_website(website_url)

    seeds = []
    if scrape_result.get("success"):
        if scrape_result.get("title"):
            seeds.append(scrape_result["title"])
        seeds.extend(scrape_result.get("headings", [])[:3])

    # Fallback seed if scraping fails or returns minimal info
    if not seeds:
        clean_url = (
            website_url.replace("https://", "")
            .replace("http://", "")
            .split("/")[0]
        )
        seeds = [clean_url]

    # Step 2: Fetch keyword ideas via Google Ads Keyword Planner API
    keyword_data = []
    try:
        keyword_data = await keyword_service.fetch_keyword_ideas(
            seed_keywords=seeds,
            customer_id=customer_id,
        )
    except Exception as e:
        logger.error(
            "Keyword fetching failed during onboarding for org %s: %s",
            org_id,
            e,
        )

    return {
        "org_id": org_id,
        "website_analysis": scrape_result,
        "seed_keywords_used": seeds,
        "keyword_opportunities_count": len(keyword_data),
        "top_keywords": keyword_data[:10],
    }
