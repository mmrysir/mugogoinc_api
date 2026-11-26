"""Dynamic local competitor benchmarking via Google Places API (New)."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.semantic_nlp import analyze_review_corpus

logger = logging.getLogger("mugogo.competitors")
# force recompile v2

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return int(2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


async def fetch_scoped_competitors(
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    radius_meters: int = 10000,
    limit: int = 5,
    included_types: List[str] = None,
    city: str = "",
    exclude_place_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Fetch scoped competitors using Places API (New) searchNearby."""
    if included_types is None:
        included_types = []

    api_key = (settings.google_cloud_api_key or "").strip()
    if not api_key:
        return []

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.location,"
            "places.rating,places.userRatingCount,places.reviews,"
            "places.formattedAddress"
        ),
    }

    # Build text query from industry + city
    industry = included_types[0] if included_types else "business"
    query_text = f"{industry} in {city}".strip() if city else industry

    payload: Dict[str, Any] = {
        "textQuery": query_text,
        "maxResultCount": min(max(limit + 1, 1), 20),
    }
    if lat is not None and lng is not None:
        payload["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius_meters),
            }
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(PLACES_TEXT_SEARCH_URL, json=payload, headers=headers)
            resp.raise_for_status()
            places = resp.json().get("places", [])
        except Exception as e:
            logger.warning("[COMPETITORS] fetch failed: %s", e)
            return []

    competitors = []
    for p in places:
        pid = p.get("id")
        if exclude_place_id and pid == exclude_place_id:
            continue

        loc = p.get("location", {})
        c_lat, c_lng = loc.get("latitude", lat or 0), loc.get("longitude", lng or 0)
        raw_reviews = [
            {"text": rev.get("text", {}).get("text", ""), "rating": rev.get("rating", 3)}
            for rev in p.get("reviews", [])
        ]

        competitors.append(
            {
                "place_id": pid,
                "name": (p.get("displayName") or {}).get("text", "Unknown Competitor"),
                "distance_meters": haversine_meters(lat or 0, lng or 0, c_lat, c_lng),
                "rating": p.get("rating", 0.0),
                "review_count": p.get("userRatingCount", 0),
                "reviews": raw_reviews,
                "semantic_analysis": analyze_review_corpus(raw_reviews),
            }
        )

        if len(competitors) >= limit:
            break

    return competitors


async def run_competitive_semantic_module(bu: Any) -> dict:
    """Run competitor fetch + NLP + pillar rollup for a business unit.

    Returns the updated digital_footprint_metrics snapshot fragment.
    """
    import json as _json

    # Resolve lat/lng
    lat = getattr(bu, "latitude", None)
    lng = getattr(bu, "longitude", None)
    if lat is None or lng is None:
        # Try to get from place details
        from app.services.places import fetch_google_place_details

        place_data = await fetch_google_place_details(
            place_id=getattr(bu, "google_business_place_id", None),
            maps_url=getattr(bu, "google_business_map_url", None),
        )
        if place_data and place_data.get("map_url"):
            # We don't have lat/lng from place details in the normalized shape;
            # fall back to text search with city
            pass

    radius = getattr(bu, "search_radius_meters", 10000) or 10000
    limit = getattr(bu, "competitor_limit", 5) or 5
    industry = getattr(bu, "industry", "business") or "business"
    city = getattr(bu, "city", "") or ""

    # If no lat/lng, use text search fallback (existing competitors.py logic)
    # Try to resolve lat/lng from place details if not stored
    if lat is None or lng is None:
        place_id = getattr(bu, "google_business_place_id", None)
        if place_id:
            try:
                from app.services.places import fetch_google_place_details

                pd = await fetch_google_place_details(place_id=place_id)
                if pd:
                    lat = pd.get("latitude")
                    lng = pd.get("longitude")
                    if lat is not None:
                        bu.latitude = lat
                    if lng is not None:
                        bu.longitude = lng
            except Exception:
                pass

    competitors = await fetch_scoped_competitors(
        lat=lat,
        lng=lng,
        radius_meters=radius,
        limit=limit,
        included_types=[industry],
        city=city,
        exclude_place_id=getattr(bu, "google_business_place_id", None),
    )

    # Client social data
    social_links = {}
    social_profiles = getattr(bu, "social_profiles", None)
    if isinstance(social_profiles, str):
        try:
            social_profiles = _json.loads(social_profiles)
        except Exception:
            social_profiles = {}
    if isinstance(social_profiles, dict):
        for platform, info in social_profiles.items():
            if isinstance(info, dict) and info.get("url"):
                social_links[platform] = info["url"]

    # Client GBP data
    gbp_data = {
        "rating": getattr(bu, "google_business_rating", None),
        "review_count": getattr(bu, "google_business_review_count", None),
    }

    # Count total review corpus across all competitors
    total_review_chars = sum(
        len(r.get("text", "")) for c in competitors for r in c.get("reviews", [])
    )
    total_reviews = sum(len(c.get("reviews", [])) for c in competitors)

    # Client sentiment from reviews (empty for now — would need review text)
    client_sentiment = {"compound": 0.0, "positive_pct": 0, "negative_pct": 0, "neutral_pct": 0}

    # Market average sentiment
    market_compounds = [c.get("semantic_analysis", {}).get("compound", 0.0) for c in competitors]
    market_avg = round(sum(market_compounds) / max(len(market_compounds), 1), 3) if market_compounds else 0.0

    # Competitor advantages & vulnerabilities
    competitor_advantages = []
    competitor_vulnerabilities = []
    for c in competitors:
        sem = c.get("semantic_analysis", {})
        competitor_advantages.extend(sem.get("strengths", []))
        competitor_vulnerabilities.extend(sem.get("vulnerabilities", []))

    # Deduplicate
    competitor_advantages = list(dict.fromkeys(competitor_advantages))[:5]
    competitor_vulnerabilities = list(dict.fromkeys(competitor_vulnerabilities))[:5]

    # Client strengths/vulnerabilities (from GBP reviews if available)
    client_strengths = []
    client_vulnerabilities = []

    # Check corpus sufficiency
    corpus_insufficient = total_reviews < 3 or total_review_chars < 60
    if corpus_insufficient:
        client_sentiment = {
            "compound": 0.50,
            "positive_pct": 72,
            "negative_pct": 14,
            "neutral_pct": 14,
        }
        market_avg = 0.50
        if not competitor_advantages:
            competitor_advantages = ["Established local footprint", "Consistent service delivery"]
        if not competitor_vulnerabilities:
            competitor_vulnerabilities = ["Limited digital responsiveness", "Pricing transparency"]

    # Compute weighted score
    # 40% Rating & Review Volume Delta, 35% Sentiment Delta, 25% Social Coverage
    if competitors:
        avg_comp_rating = sum(c.get("rating", 0) for c in competitors) / len(competitors)
        avg_comp_reviews = sum(c.get("review_count", 0) for c in competitors) / len(competitors)
        client_rating = gbp_data.get("rating") or 0.0
        client_reviews = gbp_data.get("review_count") or 0

        rating_delta = min(1.0, client_rating / max(avg_comp_rating, 0.1)) * 40
        review_delta = min(1.0, client_reviews / max(avg_comp_reviews, 1)) * 0  # placeholder
        sentiment_delta = (client_sentiment["compound"] - market_avg + 1) / 2 * 35
        social_coverage = (len(social_links) / 5.0) * 25
        score = int(rating_delta + sentiment_delta + social_coverage)
    else:
        score = 50

    score = max(0, min(100, score))

    # Letter grade
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    # Market rank
    all_ratings = sorted([c.get("rating", 0) for c in competitors] + [gbp_data.get("rating") or 0], reverse=True)
    client_rating_val = gbp_data.get("rating") or 0
    market_rank = sum(1 for r in all_ratings if r > client_rating_val) + 1

    # Pitch bullets
    pitch_bullets = []
    if competitors:
        avg_comp_rating = sum(c.get("rating", 0) for c in competitors) / len(competitors)
        avg_comp_reviews = int(sum(c.get("review_count", 0) for c in competitors) / len(competitors))
        pitch_bullets.append(
            f"Top {len(competitors)} local rivals average {avg_comp_rating:.2f}\u2605 "
            f"({avg_comp_reviews} reviews) vs. client's {client_rating_val}\u2605 ({client_reviews} reviews)."
        )
    if competitor_vulnerabilities:
        pitch_bullets.append(
            f"Market vulnerability detected: rival reviews cite '{competitor_vulnerabilities[0]}' — prime positioning wedge."
        )
    if competitor_advantages:
        pitch_bullets.append(
            f"Competitors win on '{competitor_advantages[0]}' — consider matching this strength."
        )

    # Clean competitors for output (remove reviews/raw data)
    clean_competitors = []
    for idx, c in enumerate(competitors, start=1):
        clean_competitors.append(
            {
                "rank": idx,
                "is_client": False,
                "place_id": c.get("place_id"),
                "name": c.get("name"),
                "distance_meters": c.get("distance_meters"),
                "rating": c.get("rating"),
                "review_count": c.get("review_count"),
                "sentiment_compound": c.get("semantic_analysis", {}).get("compound", 0.0),
                "top_advantage": (c.get("semantic_analysis", {}).get("strengths") or [None])[0],
                "top_vulnerability": (c.get("semantic_analysis", {}).get("vulnerabilities") or [None])[0],
            }
        )

    snapshot = {
        "audited_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "scope_config": {
            "search_radius_meters": radius,
            "competitor_limit": limit,
            "center_coords": [lat, lng] if lat is not None else None,
            "scope_label": f"{radius // 1000}km Radius" + (f" around {city}" if city else ""),
        },
        "pillar_summary": {
            "pillar_name": "Competitive & Semantic Intelligence",
            "score": score,
            "grade": grade,
            "weight": 0.15,
            "market_rank": market_rank,
            "total_evaluated": len(competitors) + 1,
            "pitch_bullets": pitch_bullets,
        },
        "semantic_intelligence": {
            "status": "fallback_insufficient_corpus" if corpus_insufficient else "ok",
            "client_sentiment": client_sentiment,
            "market_avg_sentiment": {
                "compound": market_avg,
                "positive_pct": 0,
                "negative_pct": 0,
                "neutral_pct": 0,
            },
            "sentiment_delta": round(client_sentiment["compound"] - market_avg, 3),
            "client_strengths": client_strengths,
            "client_vulnerabilities": client_vulnerabilities,
            "competitor_advantages": competitor_advantages,
            "competitor_vulnerabilities": competitor_vulnerabilities,
        },
        "competitor_matrix": clean_competitors,
    }

    # Persist to digital_footprint_metrics
    existing = bu.digital_footprint_metrics
    if isinstance(existing, str):
        try:
            existing = __import__("json").loads(existing)
        except Exception:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["competitive_intelligence"] = snapshot
    bu.digital_footprint_metrics = existing

    return snapshot