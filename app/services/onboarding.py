import logging
from typing import Optional

from app.services.scraper import scrape_client_website
from app.services.keywords import keyword_service
from app.services.social_discovery import discover_social_presence

logger = logging.getLogger("mugogo.onboarding")

async def analyze_and_seed_entity(org_id: str, website_url: str, customer_id: Optional[str] = None) -> dict:
    """
    1. Scrapes the client's website to extract core text/headings.
    2. Builds automated seed keywords from the business's own site.
    3. Fetches live search volume ideas using the Google Ads API.
    """
    # Step 1: Scrape website metadata
    scrape_result = await scrape_client_website(website_url)
    
    seeds = []
    if scrape_result.get("success"):
        if scrape_result.get("title"):
            seeds.append(scrape_result["title"])
        seeds.extend(scrape_result.get("headings", [])[:3])
        
    # Fallback seed if scraping fails or returns minimal info
    if not seeds:
        clean_url = website_url.replace("https://", "").replace("http://", "").split("/")[0]
        seeds = [clean_url]
        
    # Step 2: Fetch Keyword Ideas via Google Ads API
    keyword_data = []
    try:
        # Await the async fetch_keyword_ideas method on the singleton instance
        keyword_data = await keyword_service.fetch_keyword_ideas(
            seed_keywords=seeds,
            customer_id=customer_id
        )
    except Exception as e:
        logger.error(f"Keyword fetching failed during onboarding for org {org_id}: {e}")
        
    # Step 3: Discover Social Presence
    social_insights = await discover_social_presence(
        brand_name=scrape_result.get("title") or website_url,
        niche=website_url
    )
    
    return {
        "org_id": org_id,
        "website_analysis": scrape_result,
        "seed_keywords_used": seeds,
        "keyword_opportunities_count": len(keyword_data),
        "top_keywords": keyword_data[:10], # Return top 10 preview
        "social_insights": social_insights
    }
