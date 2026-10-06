import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("mugogo.scraper")

async def scrape_client_website(url: str) -> dict:
    """
    Asynchronously fetches a client's website and extracts SEO metadata,
    headings, and main text snippets for context building.
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
                
        return {
            "success": True,
            "url": url,
            "title": title,
            "meta_description": meta_desc,
            "headings": headings[:10], # Cap to top 10 headings
        }
    except Exception as e:
        logger.error(f"Failed to scrape website {url}: {str(e)}")
        return {
            "success": False,
            "url": url,
            "error": str(e)
        }
