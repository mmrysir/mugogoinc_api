import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

SOCIAL_PATTERNS = {
    "linkedin": r"(?:https?:\/\/)?(?:www\.)?linkedin\.com\/(?:in|company)\/([a-zA-Z0-9\-_%]+)",
    "facebook": r"(?:https?:\/\/)?(?:www\.)?facebook\.com\/([a-zA-Z0-9\-_%.]+)",
    "instagram": r"(?:https?:\/\/)?(?:www\.)?instagram\.com\/([a-zA-Z0-9\-_%.]+)",
    "twitter": r"(?:https?:\/\/)?(?:www\.)?(?:twitter|x)\.com\/([a-zA-Z0-9\-_%.]+)",
    "youtube": r"(?:https?:\/\/)?(?:www\.)?youtube\.com\/(?:c\/|channel\/|@)([a-zA-Z0-9\-_%.]+)",
    "tiktok": r"(?:https?:\/\/)?(?:www\.)?tiktok\.com\/@([a-zA-Z0-9\-_%.]+)",
    "google_business": r"(?:https?:\/\/)?(?:www\.)?(?:google\.[a-z.]+\/maps|maps\.google\.[a-z.]+|goo\.gl\/maps)\/[^\s\"'>]+",
}

# Exclusion keywords (sharing buttons, widget links, login redirects)
EXCLUDED_SUBPATHS = [
    "/share",
    "/sharer",
    "/intent",
    "/widgets",
    "/login",
    "/signup",
    "/plugins",
    "/v1/",
    "/v2/",
    "sharer.php",
]


def clean_social_url(raw_url: str) -> str:
    """Standardize social profile URL to https://domain/path."""
    if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
        raw_url = f"https://{raw_url}"
    parsed = urlparse(raw_url)
    scheme = "https"
    netloc = parsed.netloc.lower()
    # For Google Maps, preserve query params (cid, q, etc.)
    if "maps" in netloc or "goo.gl" in netloc:
        path = parsed.path.rstrip("/")
        if not path and parsed.query:
            path = "/"
        query_part = f"?{parsed.query}" if parsed.query else ""
        return f"{scheme}://{netloc}{path}{query_part}"
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def extract_social_links(urls: List[str]) -> Dict[str, str]:
    """
    Scans a list of URLs and extracts unique, verified social media profile links.
    Filters out sharing widgets and authorization links.
    """
    discovered_socials: Dict[str, str] = {}

    for url in urls:
        if not url:
            continue
        lower_url = url.lower()
        if any(bad in lower_url for bad in EXCLUDED_SUBPATHS):
            continue

        for platform, pattern in SOCIAL_PATTERNS.items():
            if platform in discovered_socials:
                continue

            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                discovered_socials[platform] = clean_social_url(url)

    return discovered_socials
