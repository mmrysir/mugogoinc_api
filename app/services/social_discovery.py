"""
social_discovery.py — DEPRECATED

The Google SERP DOM scraping approach (BeautifulSoup + google.com search URLs)
has been removed for the following reasons:

1. Google actively blocks and rate-limits automated SERP requests.
2. Parsing Google's HTML DOM is brittle and breaks on every layout change.
3. Scraping google.com violates Google's Terms of Service (Section 5.3).

Replacement strategy (D3 decision, 2026-10-07):
  Social media links are stored directly in `BusinessUnit.social_media_links`
  via the Edit Business Profile modal in the UI.

  Administrators enter official profile handles during onboarding and can
  update them at any time via PUT /api/v1/business-units/{id}/profile.

This file is intentionally kept as a stub so existing imports in
`onboarding.py` do not cause a hard crash before that file is updated.
"""

import logging

logger = logging.getLogger("mugogo.social_discovery")


async def discover_social_presence(brand_name: str, niche: str) -> dict:
    """
    DEPRECATED — returns an empty result instead of scraping Google SERPs.

    Social links are now entered manually via the Business Unit profile form
    and stored in BusinessUnit.social_media_links.
    """
    logger.info(
        "[SOCIAL_DISCOVERY] Skipped — social links are managed via the BU "
        "profile form (brand_name=%s). No scraping performed.",
        brand_name,
    )
    return {
        "brand_name": brand_name,
        "niche": niche,
        "discovered_socials": {},
        "note": "Automatic social discovery is disabled. "
                "Update social links via the Business Unit profile.",
    }
