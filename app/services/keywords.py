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
        location_id: Optional[str] = None,
        language: Optional[str] = None,
        location_ids: Optional[List[str]] = None,
        language_ids: Optional[List[str]] = None,
        page_url: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch keyword metrics for given seed phrases across multiple target locations and languages.
        Returns search volume, competition level, low top-of-page bid, high top-of-page bid.
        """
        # Consolidate location IDs
        loc_list: List[str] = []
        if location_ids:
            for lid in location_ids:
                if lid and str(lid) not in loc_list:
                    loc_list.append(str(lid))
        if location_id and str(location_id) not in loc_list:
            loc_list.append(str(location_id))
        if not loc_list:
            loc_list = ["2404"]  # Default: Kenya

        # Consolidate language IDs
        lang_list: List[str] = []
        if language_ids:
            for lid in language_ids:
                if lid and str(lid) not in lang_list:
                    lang_list.append(str(lid))
        if language and str(language) not in lang_list:
            lang_list.append(str(language))
        if not lang_list:
            lang_list = ["1000"]  # Default: English

        logger.info(
            "[KEYWORD_SERVICE] Fetching keyword metrics for seeds=%s, locations=%s, languages=%s, url=%s, customer_id=%s",
            seed_keywords, loc_list, lang_list, page_url, customer_id,
        )

        if not settings.google_ads_developer_token:
            logger.warning("[KEYWORD_SERVICE] Google Ads credentials not configured. Returning empty.")
            return []

        # Resolve effective customer ID: per-request arg → global config fallback
        effective_customer_id = (
            customer_id or settings.google_ads_customer_id
        ).replace("-", "")

        sorted_seeds = ",".join(sorted(k.strip() for k in seed_keywords if k.strip()))
        sorted_locs = ",".join(sorted(loc_list))
        sorted_langs = ",".join(sorted(lang_list))
        cache_key = f"{sorted_seeds}_{sorted_locs}_{sorted_langs}_{page_url or ''}_{effective_customer_id}"

        if cache_key in _CACHE:
            expire_time, data = _CACHE[cache_key]
            if time.time() < expire_time:
                logger.info("[KEYWORD_SERVICE] Returning cached keyword metrics")
                return data
            else:
                del _CACHE[cache_key]

        valid_keywords = [k.strip() for k in seed_keywords if k.strip()] if seed_keywords else []

        def _fetch_for_lang(target_lang: str) -> List[Dict[str, Any]]:
            client = get_google_ads_client()
            keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")

            request = client.get_type("GenerateKeywordIdeasRequest")
            request.customer_id = effective_customer_id
            request.language = f"languageConstants/{target_lang}"

            # Google Ads API natively accepts multiple geo-target constants in a single request
            for loc in loc_list:
                request.geo_target_constants.append(f"geoTargetConstants/{loc}")

            if valid_keywords and page_url:
                request.keyword_and_url_seed.url = page_url
                request.keyword_and_url_seed.keywords.extend(valid_keywords)
            elif valid_keywords:
                request.keyword_seed.keywords.extend(valid_keywords)
            elif page_url:
                request.url_seed.url = page_url

            request.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

            try:
                response = keyword_plan_idea_service.generate_keyword_ideas(request=request)

                batch_results: List[Dict[str, Any]] = []
                for idea in response:
                    metrics = idea.keyword_idea_metrics

                    monthly_search_volume = getattr(metrics, "avg_monthly_searches", 0)
                    competition = getattr(metrics.competition, "name", "UNKNOWN")

                    low_bid_micros = getattr(metrics, "low_top_of_page_bid_micros", 0) or 0
                    high_bid_micros = getattr(metrics, "high_top_of_page_bid_micros", 0) or 0

                    low_bid = low_bid_micros / 1_000_000
                    high_bid = high_bid_micros / 1_000_000

                    vol = monthly_search_volume or 0

                    batch_results.append({
                        "keyword": idea.text,
                        "monthly_search_volume": vol,
                        "competition": competition,
                        "low_top_of_page_bid_usd": round(low_bid, 2),
                        "high_top_of_page_bid_usd": round(high_bid, 2),
                        "opportunity_score": min(98, max(40, int(vol / 50))),
                    })
                return batch_results
            except GoogleAdsException as ex:
                logger.error(f"[KEYWORD_SERVICE] Google Ads API Exception for lang={target_lang}: {ex}")
                return []
            except Exception as e:
                logger.error(f"[KEYWORD_SERVICE] Error for lang={target_lang}: {e}")
                return []

        # Run queries across languages and aggregate/deduplicate results
        all_ideas: Dict[str, Dict[str, Any]] = {}
        comp_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}

        for lang in lang_list:
            batch = await asyncio.to_thread(_fetch_for_lang, lang)
            for idea in batch:
                kw = idea["keyword"].lower().strip()
                if kw not in all_ideas:
                    all_ideas[kw] = dict(idea)
                else:
                    existing = all_ideas[kw]
                    # Sum search volumes across languages
                    existing["monthly_search_volume"] += idea.get("monthly_search_volume", 0)

                    # Lowest non-zero top-of-page bid
                    curr_low = existing.get("low_top_of_page_bid_usd", 0.0)
                    new_low = idea.get("low_top_of_page_bid_usd", 0.0)
                    if curr_low == 0.0:
                        existing["low_top_of_page_bid_usd"] = new_low
                    elif new_low > 0.0:
                        existing["low_top_of_page_bid_usd"] = min(curr_low, new_low)

                    # Highest top-of-page bid
                    existing["high_top_of_page_bid_usd"] = max(
                        existing.get("high_top_of_page_bid_usd", 0.0),
                        idea.get("high_top_of_page_bid_usd", 0.0),
                    )

                    # Take highest competition tier
                    if comp_rank.get(idea.get("competition", ""), 0) > comp_rank.get(existing.get("competition", ""), 0):
                        existing["competition"] = idea["competition"]

                    # Recalculate opportunity score
                    vol = existing["monthly_search_volume"]
                    existing["opportunity_score"] = min(98, max(40, int(vol / 50)))

        results = sorted(all_ideas.values(), key=lambda x: x.get("monthly_search_volume", 0), reverse=True)

        # Cache results if successful
        if results:
            _CACHE[cache_key] = (time.time() + CACHE_TTL, results)

        return results


keyword_service = KeywordPlannerService()
