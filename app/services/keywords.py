from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict, List, Optional
from google.ads.googleads.errors import GoogleAdsException
import time

from app.services.google_ads import get_google_ads_client
from app.core.config import settings

logger = logging.getLogger("mugogo.keywords")

_CACHE = {}
CACHE_TTL = 3600

LANGUAGE_ENGLISH = "languageConstants/1000"

class KeywordPlannerService:
    """
    Google Ads Keyword Planner Connector.
    Retrieves monthly search volume, competition tier, and top-of-page bid ranges
    for seed phrases (e.g. '3 day masai mara safari', 'tax compliance kenya').
    """

    def __init__(self, developer_token: Optional[str] = None):
        self.developer_token = developer_token

    async def fetch_keyword_ideas(
        self,
        seed_keywords: List[str],
        location_id: str = "2404",
        language: str = "1000",
        page_url: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch keyword metrics for given seed phrases.
        Returns search volume, competition level, low top-of-page bid, high top-of-page bid.

        Args:
            customer_id: Override the global Google Ads customer ID for this call.
                         If not provided, falls back to ``settings.google_ads_customer_id``.
                         Pass the per-org value from ``ClientMarketingChannel.google_ads_customer_id``.
        """
        logger.info(
            "[KEYWORD_SERVICE] Fetching keyword metrics for seeds: %s, url: %s, customer_id: %s",
            seed_keywords, page_url, customer_id,
        )

        if not settings.google_ads_developer_token:
            logger.warning("[KEYWORD_SERVICE] Google Ads credentials not configured. Returning empty.")
            return []

        # Resolve effective customer ID: per-request arg → global config fallback
        effective_customer_id = (
            customer_id or settings.google_ads_customer_id
        ).replace("-", "")

        cache_key = f"{','.join(seed_keywords)}_{location_id}_{language}_{page_url}_{effective_customer_id}"
        if cache_key in _CACHE:
            expire_time, data = _CACHE[cache_key]
            if time.time() < expire_time:
                logger.info("[KEYWORD_SERVICE] Returning cached keyword metrics")
                return data
            else:
                del _CACHE[cache_key]

        def _fetch():
            client = get_google_ads_client()
            keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

            request = client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = effective_customer_id
            request.language = f"languageConstants/{language}"
            
            request.geo_target_constants.append(f"geoTargetConstants/{location_id}")
            if seed_keywords:
                request.keyword_seed.keywords.extend([k.strip() for k in seed_keywords if k.strip()])
                
            if page_url:
                request.page_url.url = page_url
                
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
        results = await asyncio.to_thread(_fetch)
        
        # Cache results if successful
        if results:
            _CACHE[cache_key] = (time.time() + CACHE_TTL, results)
            
        return results


keyword_service = KeywordPlannerService()
