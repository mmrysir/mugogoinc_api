"""GSC Search Analytics telemetry service.

Handles:
- Token refresh (Google OAuth 2.0 refresh flow via httpx)
- Search Analytics API query (28-day window, dimensions: query + page)
- Metric aggregation and persistence to business_units.gsc_metrics
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business_unit import BusinessUnit

logger = logging.getLogger("mugogo.gsc_telemetry")

# Google OAuth endpoints
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SEARCH_ANALYTICS_URL = (
    "https://www.googleapis.com/webmasters/v3/sites/{site_url}/searchAnalytics/query"
)


def _get_client_id() -> str:
    """Return the GSC web client ID. Raises if not configured.

    Never falls back to google_ads_client_id — that is a Desktop client
    and Google will reject it for web redirect OAuth flows.
    """
    client_id = settings.gsc_client_id
    if not client_id:
        raise RuntimeError(
            "GSC_CLIENT_ID is not set in .env. "
            "Add it to the server .env and re-deploy."
        )
    return client_id


def _get_client_secret() -> str:
    """Return the GSC web client secret. Raises if not configured."""
    secret = settings.gsc_client_secret
    if not secret:
        raise RuntimeError(
            "GSC_CLIENT_SECRET is not set in .env. "
            "Add it to the server .env and re-deploy."
        )
    return secret


async def _refresh_access_token(refresh_token: str) -> Optional[str]:
    """Exchange a refresh token for a new short-lived access token.

    Returns the new access token string, or None on failure.
    """
    payload = {
        "client_id": _get_client_id(),
        "client_secret": _get_client_secret(),
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(_TOKEN_URL, data=payload)
            resp.raise_for_status()
            data = resp.json()
            access_token = data.get("access_token")
            logger.info("[GSC_TELEMETRY] Token refresh successful.")
            return access_token
    except Exception as exc:
        logger.error("[GSC_TELEMETRY] Token refresh failed: %s", exc)
        return None


async def fetch_gsc_search_analytics(
    site_url: str,
    access_token: str,
    row_limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """Query the GSC Search Analytics API for the past 28 days.

    Returns a dict with:
        rows      – list of raw row objects from the API
        summary   – aggregated totals (clicks, impressions, ctr, position)
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=28)

    url = _SEARCH_ANALYTICS_URL.format(
        site_url=httpx.URL(site_url).path if site_url.startswith("http") else site_url
    )
    # Use the raw site_url as-is (Google expects URL-encoded path in the endpoint)
    url = _SEARCH_ANALYTICS_URL.replace("{site_url}", site_url)

    payload = {
        "startDate": start_date.strftime("%Y-%m-%d"),
        "endDate": end_date.strftime("%Y-%m-%d"),
        "dimensions": ["query", "page"],
        "rowLimit": row_limit,
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[GSC_TELEMETRY] Search Analytics request failed %d: %s",
            exc.response.status_code,
            exc.response.text,
        )
        return None
    except Exception as exc:
        logger.error("[GSC_TELEMETRY] Search Analytics request error: %s", exc)
        return None

    rows: List[Dict[str, Any]] = data.get("rows", [])

    # ── Aggregate summary ────────────────────────────────────────────────────
    total_clicks = 0
    total_impressions = 0
    total_ctr_sum = 0.0
    total_position_sum = 0.0

    for row in rows:
        total_clicks += row.get("clicks", 0)
        total_impressions += row.get("impressions", 0)
        total_ctr_sum += row.get("ctr", 0.0)
        total_position_sum += row.get("position", 0.0)

    n = len(rows) or 1  # avoid division by zero
    avg_ctr = round(total_ctr_sum / n * 100, 2)  # expressed as percentage
    avg_position = round(total_position_sum / n, 1)

    # ── Top queries (deduplicated, sorted by clicks desc) ────────────────────
    query_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        keys = row.get("keys", [])
        query = keys[0] if keys else ""
        page = keys[1] if len(keys) > 1 else ""
        if query:
            if query not in query_map:
                query_map[query] = {
                    "query": query,
                    "page": page,
                    "clicks": 0,
                    "impressions": 0,
                    "ctr": 0.0,
                    "position": 0.0,
                    "_count": 0,
                }
            entry = query_map[query]
            entry["clicks"] += row.get("clicks", 0)
            entry["impressions"] += row.get("impressions", 0)
            entry["ctr"] += row.get("ctr", 0.0)
            entry["position"] += row.get("position", 0.0)
            entry["_count"] += 1

    top_queries = []
    for entry in sorted(query_map.values(), key=lambda x: x["clicks"], reverse=True)[:25]:
        count = entry.pop("_count", 1) or 1
        entry["ctr"] = round(entry["ctr"] / count * 100, 2)
        entry["position"] = round(entry["position"] / count, 1)
        top_queries.append(entry)

    return {
        "summary": {
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "avg_ctr_pct": avg_ctr,
            "avg_position": avg_position,
            "rows_returned": len(rows),
            "date_range": {
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
            },
        },
        "top_queries": top_queries,
        "raw_rows": rows,
    }


async def sync_gsc_metrics_for_bu(
    bu_id: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Full sync cycle for a Business Unit:

    1. Load the BU from DB and validate token/site configuration.
    2. Refresh the access token.
    3. Query Search Analytics.
    4. Persist the result to bu.gsc_metrics.
    5. Return the fresh payload.
    """
    result = await db.execute(select(BusinessUnit).where(BusinessUnit.id == bu_id))
    bu = result.scalar_one_or_none()
    if not bu:
        raise ValueError(f"Business Unit '{bu_id}' not found.")

    site_url = bu.gsc_site_url or bu.google_search_console_site
    if not site_url:
        raise ValueError(
            "No GSC site URL configured for this Business Unit. "
            "Complete the OAuth flow first or set google_search_console_site."
        )
    if not bu.gsc_refresh_token:
        raise ValueError(
            "No GSC refresh token found. Please complete the OAuth connection flow."
        )

    logger.info("[GSC_TELEMETRY] Starting sync for BU %s / site %s", bu_id, site_url)

    # 1. Refresh access token
    access_token = await _refresh_access_token(bu.gsc_refresh_token)
    if not access_token:
        raise RuntimeError("Failed to refresh Google access token. Check client credentials.")

    # Persist the fresh access token (best-effort; not critical)
    bu.gsc_access_token = access_token

    # 2. Query Search Analytics
    metrics = await fetch_gsc_search_analytics(site_url, access_token)
    if metrics is None:
        raise RuntimeError(
            "GSC Search Analytics query failed. Verify site URL and token scopes."
        )

    # 3. Persist
    bu.gsc_metrics = metrics
    await db.commit()
    await db.refresh(bu)

    logger.info(
        "[GSC_TELEMETRY] Sync complete for BU %s: clicks=%d impressions=%d",
        bu_id,
        metrics["summary"]["total_clicks"],
        metrics["summary"]["total_impressions"],
    )
    return metrics
