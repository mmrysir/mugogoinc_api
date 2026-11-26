"""Hybrid social media profile auditor.

Implements a 3-tier fallback waterfall to harvest social data (follower counts,
post counts, activity status) without breaking when platforms block scrapers.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("mugogo.social_scraper")


async def audit_social_profile(platform: str, profile_url: Optional[str]) -> Dict[str, Any]:
    """Tiered fallback scraper for a single social profile URL.

    1. Validates URL presence.
    2. Attempts official API query if token is configured (future).
    3. Falls back to Open Graph (OG) meta-tag parsing via httpx + regex.
    """
    if not profile_url:
        return {"active": False, "source": "none"}

    clean_url = profile_url.strip()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(clean_url, headers=headers)
            if response.status_code != 200:
                return {"active": False, "url": clean_url, "status_code": response.status_code}

            html = response.text

            og_desc_match = re.search(
                r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']',
                html,
                re.IGNORECASE,
            )
            description = og_desc_match.group(1) if og_desc_match else ""

            followers = None
            follower_match = re.search(r"([\d,\.]+)\s+Followers", description, re.IGNORECASE)
            if follower_match:
                raw_num = follower_match.group(1).replace(",", "").replace(".", "")
                if raw_num.isdigit():
                    followers = int(raw_num)

            return {
                "active": True,
                "url": clean_url,
                "followers": followers,
                "description_snippet": description[:100],
                "source": "og_html_fallback",
            }
    except Exception as e:
        return {"active": False, "url": clean_url, "error": str(e)}