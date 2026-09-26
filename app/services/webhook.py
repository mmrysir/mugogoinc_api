from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.core.security import hmac_sign

logger = logging.getLogger("mugogo.webhook")


async def forward_to_crm(
    webhook_url: str,
    lead_id: str,
    business_unit_id: str,
    full_name: str,
    phone_number: str,
    email: Optional[str],
    source_channel: str,
    payload_meta: dict,
) -> bool:
    """HMAC-sign and POST lead data to an external CRM endpoint.

    Returns True on success, False on failure.
    """
    body = {
        "lead_id": lead_id,
        "business_unit_id": business_unit_id,
        "full_name": full_name,
        "phone_number": phone_number,
        "email": email,
        "source_channel": source_channel,
        "payload_meta": payload_meta,
    }
    body_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
    signature = await hmac_sign(body_str)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                webhook_url,
                content=body_str,
                headers={
                    "Content-Type": "application/json",
                    "X-Mugogo-Signature": f"sha256={signature}",
                    "X-Mugogo-Lead-ID": lead_id,
                },
            )
            resp.raise_for_status()
            logger.info(
                "[WEBHOOK] Forwarded lead %s to %s — status %s",
                lead_id,
                webhook_url,
                resp.status_code,
            )
            return True
    except httpx.HTTPError as exc:
        logger.error("[WEBHOOK] Failed to forward lead %s: %s", lead_id, exc)
        return False
