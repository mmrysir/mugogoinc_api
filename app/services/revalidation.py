from __future__ import annotations

import logging
from typing import Optional
import httpx

from app.core.config import settings

logger = logging.getLogger("mugogo.revalidation")


async def trigger_nextjs_revalidation(
    slug: str,
    custom_revalidate_url: Optional[str] = None,
    custom_secret: Optional[str] = None,
) -> bool:
    """
    On-Demand ISR Revalidation Webhook.
    Dispatches POST to Next.js /api/revalidate?secret=...&path=/blog/{slug}
    returns True if revalidation request succeeded.
    """
    url = custom_revalidate_url or settings.nextjs_revalidate_url
    secret = custom_secret or settings.nextjs_revalidate_secret

    if not url:
        logger.warning("[REVALIDATE] No revalidation URL configured, skipping webhook.")
        return False

    path = f"/blog/{slug}"
    params = {
        "secret": secret,
        "path": path,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info("[REVALIDATE] Triggering Next.js revalidation for path=%s target=%s", path, url)
            response = await client.post(url, params=params)
            if response.status_code == 200:
                logger.info("[REVALIDATE] Successfully revalidated %s (status 200)", path)
                return True
            else:
                logger.warning(
                    "[REVALIDATE] Failed revalidating %s — HTTP status %d: %s",
                    path,
                    response.status_code,
                    response.text,
                )
                return False
    except Exception as exc:
        logger.error("[REVALIDATE] Error calling revalidation webhook for path=%s: %s", path, exc)
        return False
