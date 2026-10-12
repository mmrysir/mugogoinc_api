# Developer Guide: Keyword Intelligence, Website Scraper & Negative Keywords Specification

This specification documents the end-to-end architecture, API contracts, frontend integration, and local diagnostic verification workflow for the **Keyword Intelligence Engine**, **Website Scraper**, and **Negative Keywords Filtering** in the Mugogo Growth OS platform.

---

## 1. Architectural Overview

The Keyword Intelligence system enables operators to extract SEO metadata from a client's website, pre-fill keyword discovery seeds, target geo-locations/languages, and enforce negative keyword exclusion lists to prevent ad waste.

```
┌─────────────────────────────────┐
│ Business Unit (bu.primary_url) │
└──────────────┬──────────────────┘
               │ (1) Auto-preview on page mount
               ▼
┌─────────────────────────────────┐
│ POST /marketing/scrape-preview │ ──► app/services/scraper.py (scrape_client_website)
└──────────────┬──────────────────┘      - Extracts <title>, <meta description>, <h1>, <h2>
               │                          - Generates suggested_seeds
               ▼
┌─────────────────────────────────┐
│ BU Command Center UI            │
│ - Scraped Intelligence Card     │
│ - Seed Keywords (include)       │
│ - Negative Keywords (exclude)   │
└──────────────┬──────────────────┘
               │ (2) Operator clicks "Run Keyword Analysis"
               ▼
┌─────────────────────────────────┐
│ POST /marketing/keywords/search │ ──► Google Ads Keyword Planner API
└──────────────┬──────────────────┘      (fetch_keyword_ideas)
               │ (3) In-memory collision filter
               ▼
       Filtered Results
       (Excludes any idea matching negative_keywords)
```

---

## 2. Backend Services & API Endpoints (`mugogoinc_api`)

### 2.1 Scraper Service (`app/services/scraper.py`)
* **Function**: `scrape_client_website(url: str) -> dict`
* **Parsing Target**:
  * `<title>` tag (trimmed)
  * Meta description (`<meta name="description">` or `<meta property="og:description">`)
  * Headings (`<h1>`, `<h2>` tags, deduplicated, capped to top 10)
  * Discovered social handles (`extract_social_links`)

### 2.2 Scrape Preview Endpoint (`app/routers/marketing.py`)
* **Path**: `POST /api/v1/marketing/scrape-preview`
* **Auth**: `SUPER_ADMIN`, `MANAGER`, `AGENT`
* **Request Schema**:
  ```json
  {
    "url": "https://www.beachholidaytours.com"
  }
  ```
* **Response Schema**:
  ```json
  {
    "success": true,
    "url": "https://www.beachholidaytours.com",
    "title": "Beach Holiday Tours & Safaris Kenya",
    "meta_description": "Luxury safari packages across Masai Mara and Diani Beach",
    "headings": ["Masai Mara 3-Day Safari", "Diani Beach Getaway"],
    "discovered_socials": {
      "instagram": "https://instagram.com/beachholidaytours"
    },
    "suggested_seeds": [
      "Beach Holiday Tours & Safaris Kenya",
      "Masai Mara 3-Day Safari",
      "Diani Beach Getaway"
    ],
    "error": null
  }
  ```

### 2.3 Keyword Search & Negative Keywords Endpoint
* **Path**: `POST /api/v1/marketing/keywords/search`
* **Auth**: `SUPER_ADMIN`, `MANAGER`, `AGENT`
* **Request Schema (`KeywordSearchRequest`)**:
  ```json
  {
    "seed_keywords": ["safari packages kenya", "nairobi day trips"],
    "negative_keywords": ["free", "cheap", "jobs", "download"],
    "location_name": "Kenya",
    "language_name": "English",
    "page_url": "https://www.beachholidaytours.com",
    "business_unit_id": "018f4a21-7b32-4d2b-9e45-123456789abc"
  }
  ```
* **Negative Keyword Filter Logic**:
  ```python
  if payload.negative_keywords:
      neg_terms = [
          n.lower().strip()
          for n in payload.negative_keywords
          if n and n.strip()
      ]
      if neg_terms:
          ideas = [
              kw for kw in ideas
              if not any(neg in kw.get("keyword", "").lower() for neg in neg_terms)
          ]
  ```

---

## 3. Frontend Integration (`mugogoinc_frontend`)

### 3.1 API Client (`src/lib/api.ts`)
* `api.marketing.scrapePreview(url: string)`: Calls `POST /marketing/scrape-preview`.
* `api.marketing.searchKeywords(seedKeywords, locationName, languageName, pageUrl, businessUnitId, locationId, negativeKeywords)`: Passes both `seed_keywords` and `negative_keywords` to the backend.

### 3.2 BU Command Center (`src/app/organizations/[id]/business-units/[buId]/page.tsx`)
1. **Auto-Scan on Mount**:
   * When `bu.primary_url` is loaded, the page automatically calls `api.marketing.scrapePreview(bu.primary_url)`.
   * If `keywordSeeds` is empty, it pre-populates `keywordSeeds` with `suggested_seeds.join("\n")`.
2. **Website Scraped Intelligence Card**:
   * Displays title, meta description, and top headings extracted from the site.
   * Provides an **`⚡ Auto-fill Seeds`** quick-action button.
3. **Dual Input Textareas**:
   * **Seed Keywords (Include)**: Blue/slate styling. Phrases to expand via Google Ads.
   * **Negative Keywords (Exclude)**: Rose/red border accent. Any keyword containing these terms is omitted from the results table.

---

## 4. Developer Pre-Push Validation Checklist

Run the following commands before committing or deploying code:

### 4.1 Backend AST & Syntax Verification
```bash
cd mugogoinc_api
python3 -c "import ast; ast.parse(open('app/services/scraper.py').read()); print('scraper.py: OK')"
python3 -c "import ast; ast.parse(open('app/routers/marketing.py').read()); print('marketing.py: OK')"
python3 -c "import ast; ast.parse(open('tests/test_marketing.py').read()); print('test_marketing.py: OK')"
```

### 4.2 Local Scraper Integration Test Snippet
```python
import asyncio
from app.services.scraper import scrape_client_website

async def test_scraper_execution():
    url = "https://www.beachholidaytours.com"
    data = await scrape_client_website(url)
    assert "success" in data
    print("Scraper returned:", data)

asyncio.run(test_scraper_execution())
```

### 4.3 Negative Keywords Filtering Assertion Snippet
```python
raw_results = [
    {"keyword": "luxury safari packages", "monthly_search_volume": 1200},
    {"keyword": "free safari booking guide", "monthly_search_volume": 450},
    {"keyword": "cheap safari deals", "monthly_search_volume": 800},
]
negatives = ["free", "cheap"]
neg_terms = [n.lower().strip() for n in negatives if n.strip()]

filtered = [
    kw for kw in raw_results
    if not any(neg in kw["keyword"].lower() for neg in neg_terms)
]

assert len(filtered) == 1
assert filtered[0]["keyword"] == "luxury safari packages"
print("Negative keyword exclusion verified successfully.")
```

### 4.4 Frontend TypeScript Compilation
```bash
cd mugogoinc_frontend
npx tsc --noEmit
```
Ensure this command exits with status 0 and zero TypeScript errors.

---

## 5. Production Deployment Instructions

1. **Commit and Push**:
   ```bash
   git add .
   git commit -m "feat(marketing): add scraped metadata preview and negative keywords filtering"
   git push origin main
   ```
2. **On the Production Server**:
   ```bash
   cd /path/to/Mugogoinc/mugogoinc_api
   git pull origin main
   pm2 restart mugogo-api --update-env
   ```
