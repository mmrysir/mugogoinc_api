import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("mugogo.scraper")

SOCIAL_DOMAINS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "twitter.com": "x",
    "x.com": "x",
    "linkedin.com": "linkedin",
    "youtube.com": "youtube",
}

def extract_social_links(soup: BeautifulSoup) -> dict:
    """Extract official brand social links from DOM, filtering out sharing dialogs."""
    discovered = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower_href = href.lower()
        for domain, platform in SOCIAL_DOMAINS.items():
            if domain in lower_href:
                if not any(bad in lower_href for bad in ["/share", "/intent", "/widgets", "sharer", "login"]):
                    parts = href.split(domain)
                    if len(parts) > 1 and len(parts[1].strip("/?#")) > 1:
                        if platform not in discovered:
                            discovered[platform] = href
    return discovered

async def scrape_client_website(url: str) -> dict:
    """
    Asynchronously fetches a client's website and extracts SEO metadata,
    headings, social media links, and main text snippets for context building.
    """
    if not url.startswith("http"):
        url = f"https://{url}"
        
    headers = {
        "User-Agent": "MugogoEngineBot/1.0 (+https://mugogo.co.ke)"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Extract title
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        
        # Extract meta description
        meta_desc = ""
        desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        if desc_tag and desc_tag.get("content"):
            meta_desc = desc_tag["content"].strip()
            
        # Extract main headings (h1, h2) for keyword seeding
        headings = []
        for h in soup.find_all(["h1", "h2"]):
            text = h.get_text().strip()
            if text and text not in headings:
                headings.append(text)

        # Extract social profile links
        socials = extract_social_links(soup)
                
        return {
            "success": True,
            "url": url,
            "title": title,
            "meta_description": meta_desc,
            "headings": headings[:10], # Cap to top 10 headings
            "discovered_socials": socials,
        }
    except Exception as e:
        logger.error(f"Failed to scrape website {url}: {str(e)}")
        return {
            "success": False,
            "url": url,
            "error": str(e),
            "discovered_socials": {},
        }
