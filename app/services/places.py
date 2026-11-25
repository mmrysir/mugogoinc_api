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

    try:
        parsed = urlparse(maps_url)
        cid_param = parse_qs(parsed.query).get("cid")
        if cid_param and cid_param[0].isdigit():
            return cid_param[0]
    except Exception:
        pass

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
    """Extract a human-readable business name from a Google Maps URL path."""
    if not url:
        return None
    match = re.search(r"/maps/place/([^/@]+)", url)
    if not match:
        return None
    raw_slug = match.group(1)
    return unquote(raw_slug.replace("+", " ")).strip() or None


def _api_key() -> Optional[str]:
    return (
        (getattr(settings, "google_cloud_api_key", "") or "").strip()
        or os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
        or os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
        or None
    )


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
            "X-Goog-FieldMask": "places.id,places.displayName,places.rating,places.userRatingCount,places.businessStatus,places.googleMapsUri,places.websiteUri",
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


async def fetch_google_place_details(
    cid: Optional[str] = None,
    place_id: Optional[str] = None,
    maps_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Unified hybrid resolver following strict priority:

    1. place_id  -> modern Place Details API (Places API New)
    2. maps_url   -> extract ChIJ... place_id, or fall back to text search
    3. cid        -> legacy Place Details endpoint (best-effort)
    """
    resolved_place_id = (place_id or "").strip() or None

    # If maps_url provided and no direct place_id, try to extract one
    if not resolved_place_id and maps_url:
        resolved_place_id = extract_place_id_from_url(maps_url)

    # If still no place_id but maps_url provided, try text search from URL slug
    if not resolved_place_id and maps_url:
        details = await fetch_place_details_from_url(maps_url)
        if details and details.get("place_id"):
            resolved_place_id = details["place_id"]

    # If maps_url provided and no place_id extracted, try extracting cid from URL
    resolved_cid = (cid or "").strip() or None
    if not resolved_place_id and maps_url and not resolved_cid:
        resolved_cid = extract_cid_from_maps_url(maps_url)

    api_key = _api_key()
    if not api_key:
        logger.warning("[PLACES_API] No Google API key configured.")
        return None

    # Priority 1: Modern Place ID Lookup (Places API New)
    if resolved_place_id:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"https://places.googleapis.com/v1/places/{resolved_place_id}"
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
                        "place_id": resolved_place_id,
                        "name": name_str,
                        "rating": data.get("rating"),
                        "review_count": data.get("userRatingCount"),
                        "business_status": data.get("businessStatus"),
                        "map_url": data.get("googleMapsUri"),
                    }
                logger.warning("[PLACES_API] Place Details returned %d: %s", resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning("[PLACES_API] Place Details error: %s", e)

    # Priority 2 & 3: Legacy CID Lookup (best-effort)
    if resolved_cid:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url_legacy = f"https://maps.googleapis.com/maps/api/place/details/json?cid={resolved_cid}&key={api_key}"
            try:
                resp_legacy = await client.get(url_legacy)
                if resp_legacy.status_code == 200:
                    legacy_data = resp_legacy.json()
                    if legacy_data.get("status") == "OK":
                        res = legacy_data.get("result", {}) or {}
                        return {
                            "place_id": res.get("place_id") or resolved_place_id,
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