from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("mugogo.whatsapp")


async def send_whatsapp_message(
    to_phone: str,
    text_message: str,
    max_retries: int = 3,
    initial_backoff_sec: float = 1.0,
) -> bool:
    """
    Send a raw text WhatsApp message via Meta Cloud API with exponential backoff retries.
    """
    if not settings.wa_phone_number_id or not settings.wa_access_token:
        logger.info("[WA STUB] Would send message to %s: %s", to_phone, text_message[:50])
        return True

    recipient = to_phone.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": text_message},
    }

    backoff = initial_backoff_sec
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    settings.wa_api_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.wa_access_token}"},
                )
                if resp.status_code in (200, 201):
                    logger.info("[WA] Message sent to %s on attempt %d", to_phone, attempt)
                    return True
                else:
                    logger.warning(
                        "[WA] Attempt %d/%d failed with status %d: %s",
                        attempt,
                        max_retries,
                        resp.status_code,
                        resp.text,
                    )
        except Exception as exc:
            logger.error("[WA] Attempt %d/%d encountered error: %s", attempt, max_retries, exc)

        if attempt < max_retries:
            await asyncio.sleep(backoff)
            backoff *= 2.0

    logger.error("[WA] Exhausted all %d retries sending message to %s", max_retries, to_phone)
    return False


async def send_lead_alert(
    owner_whatsapp_number: str,
    business_unit_name: str,
    full_name: str,
    phone_number: str,
    source_channel: str,
    extra_meta: Optional[dict] = None,
) -> bool:
    """Dispatch a WhatsApp Cloud API message to the business unit owner."""
    meta_lines = ""
    if extra_meta:
        meta_lines = "\n".join(f"• {k}: {v}" for k, v in extra_meta.items())
        meta_lines = f"\n\n*Additional info:*\n{meta_lines}"

    body = (
        f"🔔 *New Lead — {business_unit_name}*\n\n"
        f"*Name:* {full_name}\n"
        f"*Phone:* {phone_number}\n"
        f"*Source:* {source_channel.replace('_', ' ').title()}"
        f"{meta_lines}\n\n"
        f"_Powered by Mugogo Inc. Engine_"
    )

    return await send_whatsapp_message(owner_whatsapp_number, body)
