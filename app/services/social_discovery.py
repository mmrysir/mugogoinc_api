import httpx
import logging
import urllib.parse
from bs4 import BeautifulSoup

logger = logging.getLogger("mugogo.social_discovery")

async def discover_social_presence(brand_name: str, niche: str) -> dict:
    """
    Leverages Google's index to discover a client's or competitor's public 
    social media presence (Instagram, TikTok, X, Facebook) without hitting anti-bot walls.
    """
    platforms = ["instagram.com", "tiktok.com", "twitter.com", "facebook.com", "youtube.com"]
    discovered_links = {}
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for platform in platforms:
            query = f"site:{platform} {brand_name} {niche}"
            search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
            
            try:
                response = await client.get(search_url, headers=headers)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    # Extract the first valid organic search result link for that platform
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if platform in href and "/url?q=" in href:
                            # Clean Google's redirect URL format
                            clean_url = href.split("/url?q=")[1].split("&sa=")[0]
                            discovered_links[platform] = urllib.parse.unquote(clean_url)
                            break
            except Exception as e:
                logger.warning(f"Failed to discover social profile for {platform}: {e}")

    return {
        "brand_name": brand_name,
        "niche": niche,
        "discovered_socials": discovered_links
    }
