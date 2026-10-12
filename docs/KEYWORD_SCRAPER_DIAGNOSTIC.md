# Developer Diagnostic Checklist: Landing Page Scraper & Keyword Planner Integration

This document outlines how to inspect, test, and verify that landing page auto-scraping, keyword auto-population, and negative keywords filtering function correctly in the codebase prior to pushing to production.

---

## 1. Backend Verification (`app/services/` & `app/routers/`)

### 1.1 Scraper Service (`app/services/scraper.py`)
* The core website scraper function is `scrape_client_website(url: str) -> dict`.
* It extracts:
  * `<title>` tag
  * `<meta name="description">` and `og:description`
  * `<h1>`, `<h2>` header elements (capped to top 10)
  * Social handles via `extract_social_links(soup)`

### 1.2 Local Python Unit Test / Assertion
To verify the scraper locally:
```python
import asyncio
from app.services.scraper import scrape_client_website

async def test_scraper_parsing():
    url = "https://www.beachholidaytours.com"
    data = await scrape_client_website(url)
    assert data is not None
    assert "success" in data
    print("Scraped metadata:", data)

asyncio.run(test_scraper_parsing())
```

### 1.3 Scrape Preview Endpoint (`app/routers/marketing.py`)
* Endpoint: `POST /api/v1/marketing/scrape-preview`
* Accepts: `{"url": "https://example.com"}`
* Returns parsed title, description, headings, discovered socials, and an auto-derived list of `suggested_seeds`.

### 1.4 Negative Keywords Filtering (`app/routers/marketing.py`)
* Endpoint: `POST /api/v1/marketing/keywords/search`
* Supports optional `negative_keywords: List[str]`.
* Any keyword idea whose text contains any of the negative keyword phrases is filtered out prior to returning results to the client.

---

## 2. Frontend Verification (`src/app/organizations/[id]/business-units/[buId]/page.tsx`)

Verify that the Business Unit Command Center initializes the Keyword Planner using the scraped website metadata and provides Negative Keywords filtering.

### What to check:
1. **Auto-Scan on Mount**:
   * If the Business Unit has a `primary_url`, the page automatically invokes `api.marketing.scrapePreview(bu.primary_url)` on mount.
   * If `keywordSeeds` is initially empty, it pre-fills the textarea with `suggested_seeds.join("\n")`.
2. **Website Scraped Intelligence Card**:
   * Shows Title, Meta Description, and found Headings.
   * Displays an `⚡ Auto-fill Seeds` button.
3. **Dual Textarea Inputs**:
   * **Seed Keywords**: Inclusion phrases sent to Google Ads Keyword Planner.
   * **Negative Keywords**: Red-bordered exclusion textarea for terms like `free`, `cheap`, `jobs`.
4. **Execution**:
   * Clicking `Run Keyword Analysis` dispatches both seed and negative keywords to `/api/v1/marketing/keywords/search`.

---

## 3. Pre-Push Local Validation Checklist

Before committing and pushing code changes, run these local checks:

### 1. Backend Syntax & Type Check
```bash
python3 -c "import ast; ast.parse(open('app/services/scraper.py').read()); print('scraper.py: OK')"
python3 -c "import ast; ast.parse(open('app/routers/marketing.py').read()); print('marketing.py: OK')"
python3 -c "import ast; ast.parse(open('tests/test_marketing.py').read()); print('test_marketing.py: OK')"
```

### 2. Frontend TypeScript Compilation
```bash
cd mugogoinc_frontend
npx tsc --noEmit
```
Confirm `npx tsc --noEmit` exits with 0 errors.

### 3. Integration Flow Test
1. Open any Business Unit that has a valid `primary_url` (e.g. `https://www.beachholidaytours.com`).
2. Verify the **Website Scraped Intelligence** card appears and shows the extracted title/headings.
3. Verify the **Seed Keywords** input is auto-populated with suggested phrases.
4. In **Negative Keywords**, enter phrases to exclude (e.g. `cheap`, `free`).
5. Click **Run Keyword Analysis** and verify that no returned rows contain the excluded terms.
