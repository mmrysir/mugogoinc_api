"""Dynamic local competitor benchmarking via Google Places API (New)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("mugogo.competitors")


async def benchmark_local_competitors(
    industry: str,
    city: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_meters: int = 5000,
    scope_type: str = "radius",
) -> List[Dict[str, Any]]:
    """Benchmark local competitors using Google Places API (New).

    Supports flexible radius or county/metropolitan scopes.
    """
    effective_radius = radius_meters
    if scope_type == "county":
        effective_radius = 25000
    elif scope_type == "metropolitan":
        effective_radius = 40000

    api_key = (settings.google_cloud_api_key or "").strip()
    if not api_key:
        return []

    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.rating,places.userRatingCount,places.googleMapsUri,places.businessStatus",
    }

    payload: Dict[str, Any] = {"textQuery": f"{industry} in {city}"}
    if lat is not None and lng is not None:
        payload["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(effective_radius),
            }
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                places = data.get("places", [])
                return [
                    {
                        "place_id": p.get("id"),
                        "name": (p.get("displayName") or {}).get("text"),
                        "rating": p.get("rating"),
                        "review_count": p.get("userRatingCount"),
                        "map_url": p.get("googleMapsUri"),
                    }
                    for p in places[:5]
                ]
        except Exception as e:
            logger.warning("[COMPETITORS] benchmark failed: %s", e)

    return []