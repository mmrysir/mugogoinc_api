from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger("mugogo.places")


def extract_cid_from_maps_url(maps_url: Optional[str]) -> Optional[str]:
    """Extract a decimal CID from a Google Maps URL.

    Supports both '?cid=123456789' query parameters and the embedded
    '!1s0x<hex1>:0x<hex2>' hex feature ID found in standard Maps URLs.
    """
    if not maps_url:
        return None

    # 1. Explicit ?cid= query parameter
    try:
        parsed = urlparse(maps_url)
        cid_param = parse_qs(parsed.query).get("cid")
        if cid_param and cid_param[0].isdigit():
            return cid_param[0]
    except Exception:
        pass

    # 2. Embedded hex feature ID (!1s0x<hex1>:0x<hex2>)
    hex_match = re.search(r"!1s0x[0-9a-fA-F]+:0x([0-9a-fA-F]+)", maps_url)
    if hex_match:
        try:
            return str(int(hex_match.group(1), 16))
        except ValueError:
            return None

    return None


def _api_key() -> Optional[str]:
    return (
        (getattr(settings, "google_cloud_api_key", "") or "").strip()
        or os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        or os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
        or None
    )


async def fetch_google_place_details(
    place_id: Optional[str] = None,
    cid: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch live rating, review count, status, and Maps URL for a Google Place.

    Accepts either a ``place_id`` or a decimal ``cid``. Falls back across the
    Places API (New) and the legacy Place Details endpoint.
    """
    if not place_id and not cid:
        return None

    api_key = _api_key()
    if not api_key:
        logger.warning("[PLACES_API] No Google API key configured.")
        return None

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Places API (New) — only works with a place_id.
        if place_id:
            clean = place_id.strip()
            url_new = f"https://places.googleapis.com/v1/places/{clean}"
            headers_new = {
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,businessStatus,googleMapsUri",
            }
            try:
                resp = await client.get(url_new, headers=headers_new)
                if resp.status_code == 200:
                    data = resp.json()
                    display_name = data.get("displayName", {})
                    name_str = display_name.get("text") if isinstance(display_name, dict) else None
                    return {
                        "place_id": clean,
                        "name": name_str,
                        "rating": data.get("rating"),
                        "review_count": data.get("userRatingCount"),
                        "business_status": data.get("businessStatus"),
                        "map_url": data.get("googleMapsUri"),
                    }
                logger.debug("[PLACES_API] Places (New) %d: %s", resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning("[PLACES_API] Places (New) error: %s", e)

        # 2. Legacy Place Details — supports place_id and (best-effort) cid.
        url_legacy = "https://maps.googleapis.com/maps/api/place/details/json"
        params_legacy: Dict[str, Any] = {
            "fields": "place_id,name,rating,user_ratings_total,business_status,url",
            "key": api_key,
        }
        if place_id:
            params_legacy["place_id"] = place_id.strip()
        elif cid:
            params_legacy["cid"] = cid.strip()

        try:
            resp_legacy = await client.get(url_legacy, params=params_legacy)
            if resp_legacy.status_code == 200:
                legacy_data = resp_legacy.json()
                if legacy_data.get("status") == "OK":
                    res = legacy_data.get("result", {}) or {}
                    return {
                        "place_id": res.get("place_id") or (place_id.strip() if place_id else None),
                        "name": res.get("name"),
                        "rating": res.get("rating"),
                        "review_count": res.get("user_ratings_total"),
                        "business_status": res.get("business_status"),
                        "map_url": res.get("url"),
                    }
                logger.warning("[PLACES_API] Legacy returned status: %s", legacy_data.get("status"))
        except Exception as e:
            logger.error("[PLACES_API] Legacy error: %s", e)

    return None
