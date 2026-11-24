from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse, unquote

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


def extract_place_id_from_url(url: str) -> Optional[str]:
    """Extract an embedded ChIJ... Place ID from a Google Maps URL."""
    if not url:
        return None
    match = re.search(r"(ChI[a-zA-Z0-9_-]{20,})", url)
    if match:
        return match.group(1)
    return None


def extract_query_from_maps_url(url: str) -> Optional[str]:
    """Extract a human-readable business name from a Google Maps URL path.

    Example: .../maps/place/Nungwi+Sport+Fishing+Adventure+and+Transfer/@...
             -> "Nungwi Sport Fishing Adventure and Transfer"
    """
    if not url:
        return None
    match = re.search(r"/maps/place/([^/@]+)", url)
    if not match:
        return None
    raw_slug = match.group(1)
    return unquote(raw_slug.replace("+", " ")).strip() or None


async def fetch_place_details_from_url(url: str) -> Optional[Dict[str, Any]]:
    """Resolve business details from a full Google Maps URL using the modern
    Places API (New) Text Search endpoint. Returns a normalized dict.
    """
    query = extract_query_from_maps_url(url)
    if not query:
        logger.info("[PLACES_API] Could not extract business name from URL")
        return None

    api_key = _api_key()
    if not api_key:
        logger.warning("[PLACES_API] No Google API key configured.")
        return None

    logger.info("[PLACES_API] Searching modern Places API for query: %s", query)
    async with httpx.AsyncClient(timeout=15.0) as client:
        search_url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.rating,places.userRatingCount,places.businessStatus,places.googleMapsUri,places.websiteUri,places.location",
        }
        payload = {"textQuery": query}
        try:
            resp = await client.post(search_url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning("[PLACES_API] Text Search failed: %d - %s", resp.status_code, resp.text[:200])
                return None
            places = resp.json().get("places", []) or []
            if not places:
                logger.info("[PLACES_API] No places found for query")
                return None
            place = places[0]
            return {
                "place_id": place.get("id"),
                "name": (place.get("displayName") or {}).get("text"),
                "rating": place.get("rating"),
                "review_count": place.get("userRatingCount"),
                "business_status": place.get("businessStatus"),
                "map_url": place.get("googleMapsUri"),
                "website": place.get("websiteUri"),
            }
        except Exception as e:
            logger.warning("[PLACES_API] Text Search error: %s", e)
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
    maps_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve a Google Place using the modern Places API (New).

    Strict priority:
    1. If ``place_id`` is provided, query Place Details directly.
    2. If ``maps_url`` is provided, extract an embedded ``ChIJ...`` ID and
       query Place Details.
    3. If no embedded ID, extract the business-name slug from the Maps URL and
       run a Text Search to resolve the ``place_id``, then fetch Place Details.
    """
    target_place_id = (place_id or "").strip() or None

    # Priority 2: extract embedded ChIJ... from maps_url
    if not target_place_id and maps_url:
        target_place_id = extract_place_id_from_url(maps_url)

    # Priority 3: text search using the business-name slug from the Maps URL
    if not target_place_id and maps_url:
        query = extract_query_from_maps_url(maps_url)
        if query:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    search_url = "https://places.googleapis.com/v1/places:searchText"
                    headers = {
                        "Content-Type": "application/json",
                        "X-Goog-Api-Key": _api_key() or "",
                        "X-Goog-FieldMask": "places.id",
                    }
                    resp = await client.post(search_url, json={"textQuery": query}, headers=headers)
                    if resp.status_code == 200:
                        places = resp.json().get("places", []) or []
                        if places:
                            target_place_id = places[0].get("id")
            except Exception as e:
                logger.warning("[PLACES_API] Text search exception: %s", e)

    if not target_place_id:
        return None

    api_key = _api_key()
    if not api_key:
        logger.warning("[PLACES_API] No Google API key configured.")
        return None

    async with httpx.AsyncClient(timeout=10.0) as client:
        url = f"https://places.googleapis.com/v1/places/{target_place_id}"
        headers = {
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,businessStatus,googleMapsUri",
        }
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                display_name = data.get("displayName", {})
                name_str = display_name.get("text") if isinstance(display_name, dict) else None
                return {
                    "place_id": target_place_id,
                    "name": name_str,
                    "rating": data.get("rating"),
                    "review_count": data.get("userRatingCount"),
                    "business_status": data.get("businessStatus"),
                    "map_url": data.get("googleMapsUri"),
                }
            logger.warning("[PLACES_API] Place Details returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("[PLACES_API] Place Details error: %s", e)

    return None
