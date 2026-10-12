from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger("mugogo.places")


async def fetch_google_place_details(place_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetches live rating, review count, operational status, and Maps URL for a Google Place.
    Supports both Google Places API (New) and Places Details API (Legacy).
    """
    if not place_id or not place_id.strip():
        return None

    clean_place_id = place_id.strip()

    # Check for Places or Maps API Key
    api_key = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.warning(
            "[PLACES_API] GOOGLE_PLACES_API_KEY / GOOGLE_MAPS_API_KEY not set in environment."
        )
        return None

    # 1. Try Google Places API (New: v1/places/{id})
    url_new = f"https://places.googleapis.com/v1/places/{clean_place_id}"
    headers_new = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,businessStatus,googleMapsUri",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url_new, headers=headers_new)
            if resp.status_code == 200:
                data = resp.json()
                display_name = data.get("displayName", {})
                name_str = display_name.get("text") if isinstance(display_name, dict) else None
                return {
                    "place_id": clean_place_id,
                    "name": name_str,
                    "rating": data.get("rating"),
                    "review_count": data.get("userRatingCount"),
                    "business_status": data.get("businessStatus"),
                    "map_url": data.get("googleMapsUri"),
                }
            else:
                logger.debug(
                    "[PLACES_API] Places (New) responded %d: %s. Trying legacy endpoint...",
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as e:
            logger.warning("[PLACES_API] Places (New) request error: %s", str(e))

        # 2. Fallback to Places Details API (Legacy)
        url_legacy = "https://maps.googleapis.com/maps/api/place/details/json"
        params_legacy = {
            "place_id": clean_place_id,
            "fields": "name,rating,user_ratings_total,business_status,url",
            "key": api_key,
        }
        try:
            resp_legacy = await client.get(url_legacy, params=params_legacy)
            if resp_legacy.status_code == 200:
                legacy_data = resp_legacy.json()
                if legacy_data.get("status") == "OK":
                    res = legacy_data.get("result", {})
                    return {
                        "place_id": clean_place_id,
                        "name": res.get("name"),
                        "rating": res.get("rating"),
                        "review_count": res.get("user_ratings_total"),
                        "business_status": res.get("business_status"),
                        "map_url": res.get("url"),
                    }
                else:
                    logger.warning(
                        "[PLACES_API] Legacy Places API returned status: %s",
                        legacy_data.get("status"),
                    )
        except Exception as e:
            logger.error("[PLACES_API] Legacy Places API request error: %s", str(e))

    return None
