from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict, List, Optional
from google.ads.googleads.errors import GoogleAdsException

from app.services.google_ads import get_google_ads_client
from app.core.config import settings

logger = logging.getLogger("mugogo.keywords")

LANGUAGE_ENGLISH = "languageConstants/1000"

class KeywordPlannerService:
    """
    Google Ads Keyword Planner Connector.
    Retrieves monthly search volume, competition tier, and top-of-page bid ranges
    for seed phrases (e.g. '3 day masai mara safari', 'tax compliance kenya').
    """

    def __init__(self, developer_token: Optional[str] = None):
        self.developer_token = developer_token

    async def fetch_keyword_ideas(self, seed_keywords: List[str], location_id: str = "2404") -> List[Dict[str, Any]]:
        """
        Fetch keyword metrics for given seed phrases.
        Returns search volume, competition level, low top-of-page bid, high top-of-page bid.
        """
        logger.info("[KEYWORD_SERVICE] Fetching keyword metrics for seeds: %s", seed_keywords)

        if not settings.google_ads_developer_token:
            logger.warning("[KEYWORD_SERVICE] Google Ads credentials not configured. Returning empty.")
            return []

        def _fetch():
            client = get_google_ads_client()
            keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

            request = client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = settings.google_ads_customer_id.replace("-", "")
            request.language = LANGUAGE_ENGLISH
            
            # 2404 is Kenya. Can be overridden in frontend payload
            request.geo_target_constants.append(f"geoTargetConstants/{location_id}")
            request.keyword_seed.keywords.extend([k.strip() for k in seed_keywords if k.strip()])
            request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

            try:
                response = keyword_plan_idea_service.generate_keyword_ideas(request=request)
                
                results = []
                for idea in response:
                    metrics = idea.keyword_idea_metrics
                    
                    monthly_search_volume = getattr(metrics, "avg_monthly_searches", 0)
                    competition = getattr(metrics.competition, "name", "UNKNOWN")
                    
                    low_bid_micros = getattr(metrics, "low_top_of_page_bid_micros", 0) or 0
                    high_bid_micros = getattr(metrics, "high_top_of_page_bid_micros", 0) or 0

                    low_bid = low_bid_micros / 1_000_000
                    high_bid = high_bid_micros / 1_000_000
                    
                    vol = monthly_search_volume or 0

                    results.append({
                        "keyword": idea.text,
                        "monthly_search_volume": vol,
                        "competition": competition,
                        "low_top_of_page_bid_usd": round(low_bid, 2),
                        "high_top_of_page_bid_usd": round(high_bid, 2),
                        "opportunity_score": min(98, max(40, int(vol / 50))),
                    })
                return results
            except GoogleAdsException as ex:
                logger.error(f"[KEYWORD_SERVICE] Google Ads API Exception: {ex}")
                return []
            except Exception as e:
                logger.error(f"[KEYWORD_SERVICE] Error: {e}")
                return []

        # Run the synchronous Google Ads client call in a thread pool to avoid blocking async loop
        return await asyncio.to_thread(_fetch)


keyword_service = KeywordPlannerService()
