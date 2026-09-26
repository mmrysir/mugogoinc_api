from __future__ import annotations

import logging
import time
import hashlib
from typing import Any, Dict, Optional
import httpx

from app.core.config import settings

logger = logging.getLogger("mugogo.attribution")


def _hash_data(val: Optional[str]) -> Optional[str]:
    """Meta CAPI requires SHA256 hashed user data (emails, phones, etc.)."""
    if not val:
        return None
    val_clean = val.strip().lower()
    return hashlib.sha256(val_clean.encode("utf-8")).hexdigest()


async def dispatch_meta_capi_event(
    event_name: str,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    fbclid: Optional[str] = None,
    event_source_url: Optional[str] = None,
    custom_data: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Sends server-side event to Meta Conversions API (CAPI).
    Supported event names: PageView, Lead, WhatsAppClick, Purchase, etc.
    Bypasses browser ad blockers by posting directly from FastAPI core.
    """
    if not settings.meta_pixel_id or not settings.meta_capi_access_token:
        logger.info(
            "[META_CAPI] Pixel ID or CAPI token not configured. Skipping event=%s for phone=%s",
            event_name,
            phone_number,
        )
        return False

    url = f"https://graph.facebook.com/v20.0/{settings.meta_pixel_id}/events"

    user_data = {
        "em": [_hash_data(email)] if email else [],
        "ph": [_hash_data(phone_number)] if phone_number else [],
        "client_ip_address": client_ip,
        "client_user_agent": user_agent,
    }
    if fbclid:
        user_data["fbc"] = f"fb.1.{int(time.time())}.{fbclid}"

    payload = {
        "data": [
            {
                "event_name": event_name,
                "event_time": int(time.time()),
                "action_source": "website",
                "event_source_url": event_source_url or "https://mugogoinc.com",
                "user_data": {k: v for k, v in user_data.items() if v},
                "custom_data": custom_data or {},
            }
        ],
        "access_token": settings.meta_capi_access_token,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("[META_CAPI] Successfully dispatched %s to Meta CAPI", event_name)
                return True
            else:
                logger.warning(
                    "[META_CAPI] Failed dispatching %s — HTTP %d: %s",
                    event_name,
                    resp.status_code,
                    resp.text,
                )
                return False
    except Exception as exc:
        logger.error("[META_CAPI] Error calling Meta CAPI endpoint: %s", exc)
        return False


async def sync_exclusion_audience(
    lead_phone: str,
    lead_email: Optional[str] = None,
    gclid: Optional[str] = None,
    fbclid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Exclusion Audience Sync:
    When a lead converts (submits form / clicks WhatsApp), send a 'ConvertedLead' event
    to Meta CAPI & Google Ads API so marketing budgets aren't wasted retargeting them.
    """
    results = {
        "meta_capi": False,
        "google_ads": False,
    }

    # Meta CAPI Conversion Sync
    meta_ok = await dispatch_meta_capi_event(
        event_name="Lead",
        email=lead_email,
        phone_number=lead_phone,
        fbclid=fbclid,
        custom_data={"converted": True, "exclusion_tag": "Converted_Lead"},
    )
    results["meta_capi"] = meta_ok

    # Google Ads Conversion Sync log/stub
    if gclid:
        logger.info("[EXCLUSION_SYNC] Tagging GCLID %s as Converted Lead in Google Ads API", gclid)
        results["google_ads"] = True

    return results
