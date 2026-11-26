import os
import re
import json
import time
import asyncio
import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote_plus
import httpx
from bs4 import BeautifulSoup

from app.core.config import settings

logger = logging.getLogger("mugogo.audit")

AEO_PILLAR_NAME = "AEO & AI Discoverability"
AI_CRAWLER_BOTS = ("GPTBot", "ClaudeBot", "PerplexityBot", "CCBot", "Google-Extended")

# Entity types that answer engines use to verify who a business is.
CORE_ENTITY_SCHEMA_TYPES = {
    "Organization",
    "LocalBusiness",
    "WebSite",
    "Hotel",
    "Restaurant",
    "Store",
    "MedicalBusiness",
    "HealthAndBeautyBusiness",
    "ProfessionalService",
    "LodgingBusiness",
    "FoodEstablishment",
    "HomeAndConstructionBusiness",
    "EntertainmentBusiness",
}

# Weights are rebalanced when a pillar is added so the overall score remains 0-100.
PILLAR_WEIGHTS = {
    "Search Engine Optimization": 0.20,
    "Google Business Profile": 0.18,
    "Reputation & Reviews": 0.14,
    "Advertising & Paid Media": 0.13,
    "Content & Conversion Funnel": 0.15,
    "Social Media Presence": 0.08,
    AEO_PILLAR_NAME: 0.12,
}

# Google Places lookups backing the GBP / Maps Pack checks are quota-heavy; cache
# successful lookups briefly so consecutive audits do not re-query (or flap) on jitter.
PLACES_CACHE_TTL_SECONDS = 24 * 60 * 60
_PLACES_CACHE: Dict[str, Tuple[float, Optional[dict]]] = {}


class WebsiteAuditScanner:
    def __init__(
        self,
        target_url: str,
        business_name: str = "",
        primary_city: str = "",
        industry: str = "General Business",
        phone: Optional[str] = None,
        google_place_id: Optional[str] = None,
        maps_url: Optional[str] = None,
        gbp_verified: bool = False,
    ):
        raw_url = target_url.strip()
        if not raw_url.startswith(("http://", "https://")):
            raw_url = f"https://{raw_url}"
        self.target_url = raw_url
        self.parsed_url = urlparse(self.target_url)
        self.base_domain = f"{self.parsed_url.scheme}://{self.parsed_url.netloc}"
        self.clean_host = self.parsed_url.netloc.lower().replace("www.", "")

        self.business_name = (business_name or "").strip()
        self.primary_city = (primary_city or "").strip()
        self.industry = (industry or "General Business").strip()
        self.phone = (phone or "").strip() if phone else ""
        self.google_place_id = (google_place_id or "").strip() if google_place_id else ""
        self.maps_url = (maps_url or "").strip() if maps_url else ""
        self.gbp_verified = gbp_verified
        self.api_key = (settings.google_cloud_api_key or os.getenv("GOOGLE_CLOUD_API_KEY") or "").strip()

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def run_full_diagnostic(self) -> dict:
        html_content = ""
        status_code = 0
        final_url = self.target_url
        response_time_ms = 0

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=45.0, verify=False
        ) as client:
            start_ts = time.perf_counter()
            try:
                resp = await client.get(self.target_url, headers=self.headers)
                response_time_ms = int((time.perf_counter() - start_ts) * 1000)
                html_content = resp.text or ""
                status_code = resp.status_code
                final_url = str(resp.url)
                parsed_final = urlparse(final_url)
                self.base_domain = f"{parsed_final.scheme}://{parsed_final.netloc}"
                self.clean_host = parsed_final.netloc.lower().replace("www.", "")
            except Exception as e:
                logger.error(f"Audit fetch error for {self.target_url}: {e}")
                return self._build_unreachable_report(str(e))

            soup = BeautifulSoup(html_content, "html.parser")
            page_title = soup.title.string.strip() if soup.title and soup.title.string else ""

            # Run network-bound probes concurrently
            robots_task = self._check_robots_and_sitemap(client)
            psi_task = self._fetch_pagespeed_metrics(client, final_url)
            places_task = self._fetch_google_places_and_competitors(client, page_title)

            (robots_ok, sitemap_ok, sitemap_in_robots, robots_text), psi_data, places_data = (
                await asyncio.gather(robots_task, psi_task, places_task)
            )

        # Evaluate all 7 pillars
        seo_checks = self._evaluate_seo_and_speed(
            soup, html_content, status_code, final_url, response_time_ms,
            robots_ok, sitemap_ok, sitemap_in_robots, psi_data
        )
        gbp_checks = self._evaluate_gbp(soup, html_content, places_data)
        reputation_checks = self._evaluate_reputation(places_data)
        ads_checks = self._evaluate_advertising(html_content)
        conversion_checks = self._evaluate_conversion_funnel(soup, html_content)
        social_checks = self._evaluate_social_presence(soup, html_content)
        aeo_checks = self._evaluate_aeo(soup, html_content, robots_text)

        pillars = [
            self._score_pillar(
                "Search Engine Optimization", seo_checks,
                weight=PILLAR_WEIGHTS["Search Engine Optimization"],
            ),
            self._score_pillar(
                "Google Business Profile", gbp_checks,
                weight=PILLAR_WEIGHTS["Google Business Profile"],
            ),
            self._score_pillar(
                "Reputation & Reviews", reputation_checks,
                weight=PILLAR_WEIGHTS["Reputation & Reviews"],
            ),
            self._score_pillar(
                "Advertising & Paid Media", ads_checks,
                weight=PILLAR_WEIGHTS["Advertising & Paid Media"],
            ),
            self._score_pillar(
                "Content & Conversion Funnel", conversion_checks,
                weight=PILLAR_WEIGHTS["Content & Conversion Funnel"],
            ),
            self._score_pillar(
                "Social Media Presence", social_checks,
                weight=PILLAR_WEIGHTS["Social Media Presence"],
            ),
            self._score_pillar(
                AEO_PILLAR_NAME, aeo_checks,
                weight=PILLAR_WEIGHTS[AEO_PILLAR_NAME],
            ),
        ]
        aeo_pillar = next((p for p in pillars if p["name"] == AEO_PILLAR_NAME), None)

        all_checks = [c for p in pillars for c in p["checks"]]
        total_measured = len(all_checks)
        passed_count = sum(1 for c in all_checks if c["status"] == "strong")
        needs_work_count = sum(1 for c in all_checks if c["status"] == "needs_work")
        priority_count = sum(1 for c in all_checks if c["status"] == "priority")

        # Weighted overall score
        overall_score = round(sum(p["score"] * p["weight"] for p in pillars), 1)
        grade = self._compute_grade(overall_score)

        # Flatten findings for backwards-compatibility with existing UI/DB consumers
        flat_findings = [
            {
                "pillar": p["name"],
                "check": c["title"],
                "passed": c["status"] == "strong",
                "status": c["status"],
                "details": c["evidence"],
                "impact": c.get("impact", ""),
            }
            for p in pillars
            for c in p["checks"]
        ]

        # Build prioritized 90-day action plan from failed/needs-work items
        action_plan = self._build_action_plan(pillars)

        return {
            "url": final_url,
            "business_name": self.business_name,
            "primary_city": self.primary_city,
            "industry": self.industry,
            "status": "success",
            "grade": grade,
            "scores": {
                "overall": overall_score,
                "passed_count": passed_count,
                "needs_work_count": needs_work_count,
                "priority_count": priority_count,
                "total_count": total_measured,
            },
            "metrics": {
                "server_response_ms": response_time_ms,
                "mobile_performance": psi_data.get("mobile_score"),
                "desktop_performance": psi_data.get("desktop_score"),
                "lcp": psi_data.get("lcp"),
                "fcp": psi_data.get("fcp"),
                "google_rating": places_data.get("rating"),
                "google_reviews": places_data.get("review_count"),
                "competitor_median_reviews": places_data.get("competitor_median_reviews"),
                "maps_rank": places_data.get("maps_rank"),
            },
            "competitors": places_data.get("competitors", []),
            "gbp_profile": places_data.get("profile", {}),
            "pillars": pillars,
            "aeo_score": aeo_pillar["score"] if aeo_pillar else 0,
            "aeo_checks": aeo_pillar["checks"] if aeo_pillar else [],
            "action_plan": action_plan,
            "findings": flat_findings,
        }

    # -------------------------------------------------------------------------
    # Network Probes (Robots/Sitemap, PageSpeed Insights, Google Places New v1)
    # -------------------------------------------------------------------------
    async def _check_robots_and_sitemap(self, client: httpx.AsyncClient):
        robots_ok = False
        sitemap_ok = False
        sitemap_in_robots = False
        robots_text = ""

        try:
            r = await client.get(f"{self.base_domain}/robots.txt", headers=self.headers, timeout=8.0)
            robots_text = r.text or ""
            if r.status_code == 200 and "user-agent" in robots_text.lower():
                robots_ok = True
                if "sitemap:" in robots_text.lower():
                    sitemap_in_robots = True
        except Exception:
            pass

        try:
            s = await client.get(f"{self.base_domain}/sitemap.xml", headers=self.headers, timeout=8.0)
            if s.status_code == 200 and ("<urlset" in s.text.lower() or "<sitemapindex" in s.text.lower()):
                sitemap_ok = True
        except Exception:
            pass

        return robots_ok, sitemap_ok, sitemap_in_robots, robots_text

    async def _fetch_pagespeed_metrics(self, client: httpx.AsyncClient, url: str) -> dict:
        import urllib.parse

        encoded_url = urllib.parse.quote(url, safe="")

        # Define the two URL options: [With Key, Without Key (Public Fallback)]
        endpoints = []
        if self.api_key:
            endpoints.append(
                f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
                f"?url={encoded_url}&strategy=mobile&category=performance&category=seo&key={self.api_key}"
            )

        # Always include the public unauthenticated endpoint as backup
        endpoints.append(
            f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={encoded_url}&strategy=mobile&category=performance&category=seo"
        )

        for idx, psi_url in enumerate(endpoints):
            try:
                logger.info(f"Fetching PageSpeed (Attempt {idx + 1}) for {url}")
                res = await client.get(psi_url, timeout=35.0)

                if res.status_code == 200:
                    data = res.json()
                    lh = data.get("lighthouseResult", {})
                    cats = lh.get("categories", {})
                    audits = lh.get("audits", {})
                    perf = cats.get("performance", {}).get("score")
                    seo = cats.get("seo", {}).get("score")
                    return {
                        "mobile_score": int(round(perf * 100)) if perf is not None else None,
                        "lh_seo_score": int(round(seo * 100)) if seo is not None else None,
                        "lcp": audits.get("largest-contentful-paint", {}).get("displayValue"),
                        "fcp": audits.get("first-contentful-paint", {}).get("displayValue"),
                    }
                else:
                    logger.warning(f"PageSpeed attempt {idx + 1} returned status {res.status_code}: {res.text[:200]}")
            except Exception as e:
                logger.warning(f"PageSpeed attempt {idx + 1} exception: {str(e)}")

        return {}

    # -------------------------------------------------------------------------
    # Places API (New) v1 — searchText
    # -------------------------------------------------------------------------
    PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
    PLACES_FIELD_MASK = (
        "places.id,places.displayName,places.formattedAddress,"
        "places.nationalPhoneNumber,places.internationalPhoneNumber,"
        "places.websiteUri,places.rating,places.userRatingCount,"
        "places.businessStatus,places.regularOpeningHours,places.photos,"
        "places.reviews,places.primaryTypeDisplayName,places.location"
    )
    # Field mask for the Place Details endpoint (GET /v1/places/{id}) — no "places." prefix
    PLACES_DETAILS_FIELD_MASK = (
        "id,displayName,formattedAddress,"
        "nationalPhoneNumber,internationalPhoneNumber,"
        "websiteUri,rating,userRatingCount,"
        "businessStatus,regularOpeningHours,photos,"
        "reviews,primaryTypeDisplayName,location"
    )
    PLACES_COMPETITOR_FIELD_MASK = "places.id,places.displayName,places.rating,places.userRatingCount,places.location,places.formattedAddress"

    async def _places_text_search(self, client: httpx.AsyncClient, text_query: str, field_mask: str, max_results: int = 5, location_bias: Optional[dict] = None) -> List[Dict[str, Any]]:
        """Run a Places API (New) searchText request. Returns the list of places or []."""
        if not self.api_key or not text_query:
            return []
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": field_mask,
        }
        body: Dict[str, Any] = {"textQuery": text_query, "maxResultCount": max_results}
        if location_bias:
            body["locationBias"] = location_bias
        try:
            res = await client.post(self.PLACES_TEXT_SEARCH_URL, headers=headers, json=body, timeout=15.0)
            if res.status_code == 200:
                return res.json().get("places", []) or []
            logger.warning(f"Places API (New) returned {res.status_code}: {res.text[:300]}")
        except Exception as e:
            logger.warning(f"Places API (New) error: {e}")
        return []

    def _places_cache_key(self) -> str:
        """Stable cache key for the Places lookup backing GBP / Maps Pack checks."""
        return f"{self.clean_host}|{self.business_name}|{self.primary_city}|{self.industry}|{self.google_place_id}|{self.maps_url}"

    async def _fetch_place_details_by_id(self, client: httpx.AsyncClient, place_id: str) -> Optional[dict]:
        """Direct Places Details (New) lookup by place_id, returning the same shape as text search."""
        if not place_id or not self.api_key:
            return None
        url = f"https://places.googleapis.com/v1/places/{place_id.strip()}"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": self.PLACES_DETAILS_FIELD_MASK,
        }
        try:
            res = await client.get(url, headers=headers, timeout=15.0)
            if res.status_code == 200:
                return res.json()
            logger.warning("Places Details (New) for %s returned %d", place_id, res.status_code)
        except Exception as e:
            logger.warning("Places Details (New) request error for %s: %s", place_id, e)
        return None

    def _extract_place_id_from_maps_url(self, maps_url: str) -> Optional[str]:
        """Best-effort extraction of a place_id / cid token from a Maps URL."""
        if not maps_url:
            return None
        m = re.search(r"place_id[:=]([A-Za-z0-9_-]+)", maps_url)
        if m:
            return m.group(1)
        m = re.search(r"(ChIJ[A-Za-z0-9_-]+)", maps_url)
        if m:
            return m.group(1)
        return None

    async def _fetch_google_places_and_competitors(self, client: httpx.AsyncClient, page_title: str) -> dict:
        if not self.api_key:
            return {}

        cache_key = self._places_cache_key()
        cached = _PLACES_CACHE.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1] or {}

        # Priority 1: deterministic lookup from a saved Place ID.
        matched_place = None
        if self.google_place_id:
            matched_place = await self._fetch_place_details_by_id(client, self.google_place_id)

        # Priority 2: Maps URL lookup (only when not verified-locked)
        if not self.gbp_verified and matched_place is None and self.maps_url:
            # 2a: explicit place_id embedded in the Maps URL
            extracted = self._extract_place_id_from_maps_url(self.maps_url)
            if extracted:
                matched_place = await self._fetch_place_details_by_id(client, extracted)
            # 2c: resolve via business name parsed from the Maps URL (modern Text Search)
            if matched_place is None:
                try:
                    from app.services.places import fetch_place_details_from_url

                    details = await fetch_place_details_from_url(self.maps_url)
                    if details and details.get("place_id"):
                        matched_place = await self._fetch_place_details_by_id(client, details["place_id"])
                except Exception as e:
                    logger.warning("Maps-URL text search failed: %s", e)

        # Build prioritized search candidates (only used when not verified-locked
        # and no deterministic Place ID lookup succeeded).
        if not self.gbp_verified and matched_place is None:
            cleaned_title = re.split(r"[\|\-\–\—\:]", page_title)[0].strip() if page_title else ""
            domain_words = re.sub(r"\.[a-z]{2,6}$", "", self.clean_host).replace("-", " ").replace("_", " ")

            candidates = []
            if self.business_name:
                candidates.append(f"{self.business_name} {self.primary_city}".strip())
            if cleaned_title and cleaned_title.lower() != self.business_name.lower():
                candidates.append(f"{cleaned_title} {self.primary_city}".strip())
            if domain_words:
                candidates.append(f"{domain_words} {self.primary_city}".strip())

            for query in candidates:
                if not query:
                    continue
                places = await self._places_text_search(client, query, self.PLACES_FIELD_MASK, max_results=5)
                if not places:
                    continue
                # Inspect top 3 candidates to confirm website match
                for cand in places[:3]:
                    cand_site = (cand.get("websiteUri") or "").lower()
                    if self.clean_host and self.clean_host in cand_site:
                        matched_place = cand
                        break
                if not matched_place:
                    matched_place = places[0]
                if matched_place:
                    break

        # Competitor Map Pack Search using Industry + Primary City
        competitors = []
        maps_rank = None
        median_reviews = None
        if self.primary_city or self.industry:
            comp_query = f"{self.industry} in {self.primary_city}".strip()
            # Bias results toward the matched place's location to avoid
            # location-bias issues when the server is in a different region.
            location_bias = None
            if matched_place and matched_place.get("location"):
                loc = matched_place["location"]
                lat = loc.get("latitude")
                lng = loc.get("longitude")
                if lat is not None and lng is not None:
                    location_bias = {
                        "circle": {
                            "center": {"latitude": lat, "longitude": lng},
                            "radius": 50000.0,
                        }
                    }
            comp_places = await self._places_text_search(client, comp_query, self.PLACES_COMPETITOR_FIELD_MASK, max_results=6, location_bias=location_bias)
            matched_id = matched_place.get("id") if matched_place else None
            for idx, item in enumerate(comp_places, start=1):
                if matched_id and item.get("id") == matched_id:
                    maps_rank = idx
                elif len(competitors) < 5:
                    competitors.append({
                        "name": item.get("displayName", {}).get("text") or item.get("name"),
                        "rating": item.get("rating", 0),
                        "reviews": item.get("userRatingCount", 0),
                        "address": item.get("formattedAddress", ""),
                    })
            review_counts = [c["reviews"] for c in competitors if c.get("reviews") is not None]
            if review_counts:
                median_reviews = int(statistics.median(review_counts))

        if not matched_place:
            return {
                "found": False,
                "competitors": competitors,
                "competitor_median_reviews": median_reviews,
                "maps_rank": None,
            }

        # Parse reviews for velocity
        reviews = matched_place.get("reviews", []) or []
        now_ts = datetime.now(timezone.utc).timestamp()
        recent_90d = sum(1 for r in reviews if self._review_age_days(r, now_ts) <= 90)
        negative_recent = sum(1 for r in reviews if (r.get("rating") or 5) <= 3)

        result = {
            "found": True,
            "rating": matched_place.get("rating", 0.0),
            "review_count": matched_place.get("userRatingCount", 0),
            "competitor_median_reviews": median_reviews,
            "maps_rank": maps_rank,
            "competitors": competitors,
            "recent_90d_reviews": recent_90d,
            "negative_recent_reviews": negative_recent,
            "profile": {
                "place_id": matched_place.get("id"),
                "name": matched_place.get("displayName", {}).get("text") or matched_place.get("name"),
                "status": matched_place.get("businessStatus", "OPERATIONAL"),
                "website": matched_place.get("websiteUri", ""),
                "phone": matched_place.get("internationalPhoneNumber") or matched_place.get("nationalPhoneNumber", ""),
                "has_hours": bool(matched_place.get("regularOpeningHours")),
                "photo_count": len(matched_place.get("photos", [])),
                "types": [matched_place.get("primaryTypeDisplayName", {}).get("text")] if matched_place.get("primaryTypeDisplayName") else [],
            },
            "reviews": reviews,
        }
        _PLACES_CACHE[cache_key] = (time.time() + PLACES_CACHE_TTL_SECONDS, result)
        return result

    def _review_age_days(self, review: dict, now_ts: float) -> int:
        """Return age in days of a review based on publishTime (RFC3339)."""
        pt = review.get("publishTime")
        if not pt:
            return 9999
        try:
            # Handle RFC3339 with Z
            dt = datetime.fromisoformat(pt.replace("Z", "+00:00"))
            return int((now_ts - dt.timestamp()) / 86400)
        except Exception:
            return 9999

    # -------------------------------------------------------------------------
    # Pillar Evaluators (35+ Checks)
    # -------------------------------------------------------------------------
    def _evaluate_seo_and_speed(
        self, soup, html, status_code, final_url, response_ms,
        robots_ok, sitemap_ok, sitemap_in_robots, psi_data
    ) -> list:
        checks = []

        # 1. HTTPS
        is_https = final_url.startswith("https://")
        checks.append({
            "title": "HTTPS Encryption",
            "status": "strong" if is_https else "priority",
            "evidence": f"Site loads securely over HTTPS ({final_url})." if is_https else "Site is not enforcing HTTPS.",
            "impact": "Browsers warn visitors away from non-HTTPS sites and Google downranks them.",
        })

        # 2. Server Response Time
        checks.append({
            "title": "Server Response Speed",
            "status": "strong" if response_ms < 800 else ("needs_work" if response_ms < 2000 else "priority"),
            "evidence": f"Server responded in {response_ms} ms (HTTP {status_code}).",
            "impact": "Slow server response delays First Contentful Paint and hurts mobile conversions.",
        })

        # 3. Mobile PageSpeed Score (Google PSI)
        mob_score = psi_data.get("mobile_score")
        if mob_score is not None:
            lcp = psi_data.get("lcp") or "N/A"
            checks.append({
                "title": "Google Mobile PageSpeed Score",
                "status": "strong" if mob_score >= 70 else ("needs_work" if mob_score >= 45 else "priority"),
                "evidence": f"Google Mobile Performance score is {mob_score}/100 (LCP: {lcp}).",
                "impact": "Over 75% of local visitors browse on mobile; slow pages lose ad clicks before loading.",
            })

        # 4. Title Tag
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        t_len = len(title)
        checks.append({
            "title": "Homepage Title Tag",
            "status": "strong" if 15 <= t_len <= 65 else ("needs_work" if t_len > 0 else "priority"),
            "evidence": f'"{title}" ({t_len} characters).' if title else "Missing homepage <title> tag.",
            "impact": "The title tag is the #1 on-page signal for Google search rankings and click-through rate.",
        })

        # 5. Meta Description
        meta_desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        meta_desc = (meta_desc_tag.get("content") or "").strip() if meta_desc_tag else ""
        d_len = len(meta_desc)
        checks.append({
            "title": "Homepage Meta Description",
            "status": "strong" if 50 <= d_len <= 165 else ("needs_work" if d_len > 0 else "priority"),
            "evidence": f"{d_len} characters present." if d_len else "No meta description tag found.",
            "impact": "Missing meta descriptions force Google to auto-generate snippets, lowering search CTR.",
        })

        # 6. Single H1 Heading
        h1s = [h.get_text(strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
        checks.append({
            "title": "Single H1 Heading Structure",
            "status": "strong" if len(h1s) == 1 else "needs_work",
            "evidence": f"Found {len(h1s)} H1 heading(s){': ' + h1s[0][:50] if h1s else ''}.",
            "impact": "Pages should have exactly one primary H1 headline defining the core offer.",
        })

        # 7. Structured Business Data (JSON-LD)
        ld_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        has_biz_schema = any(
            any(k in (s.string or "") for k in ["LocalBusiness", "Organization", "HealthAndBeautyBusiness", "Hotel", "Restaurant", "Store", "MedicalBusiness"])
            for s in ld_scripts
        )
        checks.append({
            "title": "Structured Business Data (JSON-LD)",
            "status": "strong" if has_biz_schema else "priority",
            "evidence": "Valid business JSON-LD schema detected." if has_biz_schema else "No LocalBusiness/Organization JSON-LD schema found.",
            "impact": "Structured schema helps Google & AI search engines (ChatGPT/Perplexity) verify your entity.",
        })

        # 8. Image Alt Text Coverage
        imgs = soup.find_all("img")
        missing_alt = sum(1 for img in imgs if not (img.get("alt") or "").strip())
        checks.append({
            "title": "Image Alt Text Optimization",
            "status": "strong" if (len(imgs) == 0 or missing_alt <= 2) else "needs_work",
            "evidence": f"{missing_alt} of {len(imgs)} homepage images are missing alt text.",
            "impact": "Alt attributes power Google Image rankings and accessibility compliance.",
        })

        # 9. Robots.txt & XML Sitemap
        checks.append({
            "title": "Robots.txt & XML Sitemap",
            "status": "strong" if (robots_ok and (sitemap_ok or sitemap_in_robots)) else ("needs_work" if (robots_ok or sitemap_ok) else "priority"),
            "evidence": f"robots.txt: {'Found' if robots_ok else 'Missing'} · sitemap.xml: {'Found' if (sitemap_ok or sitemap_in_robots) else 'Missing'}.",
            "impact": "Without a sitemap and robots.txt, search crawlers miss deep service pages.",
        })

        # 10. Indexability (No noindex block)
        robots_meta = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
        is_noindex = bool(robots_meta and "noindex" in (robots_meta.get("content") or "").lower())
        checks.append({
            "title": "Search Engine Indexability",
            "status": "priority" if is_noindex else "strong",
            "evidence": "CRITICAL: Homepage has a noindex tag blocking Google!" if is_noindex else "No noindex restriction found on homepage.",
            "impact": "A noindex tag completely removes your site from Google Search.",
        })

        return checks

    def _evaluate_gbp(self, soup, html, places_data) -> list:
        checks = []
        found = places_data.get("found", False)
        profile = places_data.get("profile", {})

        # 1. GBP Found & Operational
        checks.append({
            "title": "Google Business Profile Verified",
            "status": "strong" if found else "priority",
            "evidence": f'Found "{profile.get("name")}" ({profile.get("status", "OPERATIONAL")}).' if found else "No matching Google Business Profile found.",
            "impact": "Your Google Business Profile drives high-intent 'near me' Map Pack calls.",
        })

        # 2. Website Linked on GBP
        gbp_site = (profile.get("website") or "").lower()
        site_linked = bool(found and self.clean_host and self.clean_host in gbp_site)
        checks.append({
            "title": "GBP Links to Website",
            "status": "strong" if site_linked else ("needs_work" if found else "priority"),
            "evidence": f"Profile links to {profile.get('website')}." if site_linked else "Website URL missing or mismatched on Google profile.",
            "impact": "Linking your live domain passes local authority directly to your website.",
        })

        # 3. Phone Number Consistency
        gbp_phone = profile.get("phone") or ""
        tel_links = [a.get("href", "") for a in soup.find_all("a", href=re.compile(r"^tel:", re.I))]
        digits_gbp = re.sub(r"\D", "", gbp_phone)[-7:] if gbp_phone else ""
        phone_on_site = bool(digits_gbp and any(digits_gbp in re.sub(r"\D", "", t) for t in tel_links))
        checks.append({
            "title": "Phone Consistency (NAP Match)",
            "status": "strong" if phone_on_site else "needs_work",
            "evidence": f"GBP phone ({gbp_phone}) matches website tel: link." if phone_on_site else (
                f"GBP lists {gbp_phone}, but no matching clickable tel: link was found on website." if gbp_phone else "No phone number verified across GBP and website."
            ),
            "impact": "Consistent Name, Address & Phone (NAP) builds local Map Pack trust.",
        })

        # 4. Opening Hours & Photos
        has_hours = profile.get("has_hours", False)
        photo_count = profile.get("photo_count", 0)
        checks.append({
            "title": "Opening Hours & Profile Media",
            "status": "strong" if (has_hours and photo_count >= 5) else "needs_work",
            "evidence": f"Opening hours: {'Listed' if has_hours else 'Missing'} · Photos detected: {photo_count}.",
            "impact": "Profiles with complete hours and fresh photos receive 42% more direction requests.",
        })

        # 5. Local Map Pack Ranking
        maps_rank = places_data.get("maps_rank")
        checks.append({
            "title": "Google Maps Category Visibility",
            "status": "strong" if (maps_rank and maps_rank <= 3) else ("needs_work" if maps_rank else "priority"),
            "evidence": (
                f"Ranked #{maps_rank} on Google Maps for '{self.industry} in {self.primary_city}'."
                if maps_rank else f"Not in top local Maps results for '{self.industry} in {self.primary_city or 'your area'}'."
            ),
            "impact": "The top 3 Google Map Pack listings capture over 65% of local mobile clicks.",
        })

        return checks

    def _evaluate_reputation(self, places_data) -> list:
        checks = []
        rating = places_data.get("rating") or 0.0
        count = places_data.get("review_count") or 0
        median = places_data.get("competitor_median_reviews")
        recent_90d = places_data.get("recent_90d_reviews", 0)

        checks.append({
            "title": "Google Star Rating",
            "status": "strong" if rating >= 4.5 else ("needs_work" if rating >= 3.8 else "priority"),
            "evidence": f"{rating}★ average across {count} Google reviews." if count > 0 else "No Google reviews found.",
            "impact": "Most customers filter out businesses rated below 4.5 stars.",
        })

        if median is not None:
            checks.append({
                "title": "Review Volume vs. Nearby Competitors",
                "status": "strong" if count >= median else "needs_work",
                "evidence": f"{count} reviews compared with a local competitor median of {median} reviews.",
                "impact": "Review volume is a primary ranking factor in Google's local algorithm.",
            })

        checks.append({
            "title": "Recent Review Velocity (90 Days)",
            "status": "strong" if recent_90d >= 2 else "needs_work",
            "evidence": f"{recent_90d} of the latest sampled reviews were posted in the past 90 days.",
            "impact": "Fresh reviews prove to both Google and prospects that your business is actively trusted.",
        })

        return checks

    def _evaluate_advertising(self, html: str) -> list:
        checks = []
        has_ga = bool(re.search(r"(gtag\(|GTM-|G-[A-Z0-9]{6,}|googletagmanager\.com)", html, re.I))
        has_meta = bool(re.search(r"(fbq\(|connect\.facebook\.net)", html, re.I))
        has_gads = bool(re.search(r"AW-[0-9]{6,}", html))
        has_other_px = bool(re.search(r"(analytics\.tiktok\.com|snap\.licdn\.com|lintrk)", html, re.I))

        checks.append({
            "title": "Website Analytics (GA4 / GTM)",
            "status": "strong" if has_ga else "priority",
            "evidence": "Google Analytics 4 / Tag Manager detected in source." if has_ga else "No GA4 or Google Tag Manager script detected.",
            "impact": "Without analytics, you are flying blind on traffic sources and conversion drop-offs.",
        })

        checks.append({
            "title": "Warm Audience Retargeting (Meta / Google Ads)",
            "status": "strong" if (has_meta or has_gads) else "priority",
            "evidence": (
                f"Detected: {', '.join([p for p, ok in [('Meta Pixel', has_meta), ('Google Ads Tag', has_gads)] if ok])}."
                if (has_meta or has_gads)
                else "No Meta Pixel or Google Ads remarketing tag found."
            ),
            "impact": "96% of first-time visitors leave without booking—without pixels, you cannot retarget them.",
        })

        checks.append({
            "title": "Multi-Channel Social Ad Readiness",
            "status": "strong" if (has_meta and has_other_px) else "needs_work",
            "evidence": "TikTok/LinkedIn + Meta ad tags active." if (has_meta and has_other_px) else "Missing secondary ad network pixels (TikTok / LinkedIn).",
            "impact": "Cross-platform pixel tracking lowers cost-per-acquisition when scaling paid campaigns.",
        })

        return checks

    def _evaluate_conversion_funnel(self, soup, html: str) -> list:
        checks = []
        text_words = len(soup.get_text(" ", strip=True).split())
        forms = soup.find_all("form")
        tel_links = soup.find_all("a", href=re.compile(r"^tel:", re.I))
        has_whatsapp = bool(re.search(r"(wa\.me/|api\.whatsapp\.com|whatsapp)", html, re.I))
        has_chat = bool(re.search(r"(tawk\.to|crisp\.chat|intercom|tidio|livechatinc|zendesk)", html, re.I))
        has_booking = bool(re.search(r"(calendly|fresha|acuityscheduling|booksy|mindbody|opentable|/book|/booking|/appointment)", html, re.I))
        has_testimonials = bool(re.search(r"(testimonial|reviews|what our clients|what guests say|rated 5)", html, re.I))
        has_privacy = bool(soup.find("a", href=re.compile(r"privacy", re.I)))
        has_viewport = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))

        checks.append({
            "title": "Lead Capture Forms",
            "status": "strong" if len(forms) > 0 else "priority",
            "evidence": f"Found {len(forms)} lead/enquiry form(s) on homepage." if forms else "No lead capture form detected on homepage.",
            "impact": "Visitors need an immediate, low-friction way to request pricing or availability.",
        })

        checks.append({
            "title": "Click-to-Call Phone Link",
            "status": "strong" if len(tel_links) > 0 else "priority",
            "evidence": f"Found {len(tel_links)} clickable tel: link(s)." if tel_links else "No clickable tel: phone link found on homepage.",
            "impact": "Mobile visitors expect to tap your phone number and call in one second.",
        })

        checks.append({
            "title": "Instant Messaging (WhatsApp / Live Chat)",
            "status": "strong" if (has_whatsapp or has_chat) else "priority",
            "evidence": (
                "WhatsApp / Live Chat widget detected."
                if (has_whatsapp or has_chat)
                else "No WhatsApp click-to-chat or live chat widget detected."
            ),
            "impact": "In East Africa & hospitality markets, WhatsApp buttons increase inbound enquiries by up to 3x.",
        })

        checks.append({
            "title": "Online Booking & Self-Scheduling",
            "status": "strong" if has_booking else "needs_work",
            "evidence": "Booking flow / scheduling link detected." if has_booking else "No automated online booking widget detected.",
            "impact": "24/7 self-booking captures high-intent customers outside business hours.",
        })

        checks.append({
            "title": "On-Page Social Proof & Testimonials",
            "status": "strong" if has_testimonials else "needs_work",
            "evidence": "Customer testimonial / review section detected." if has_testimonials else "No explicit customer testimonials displayed on homepage.",
            "impact": "Displaying your 5-star reviews next to CTAs significantly boosts conversion rate.",
        })

        checks.append({
            "title": "Mobile Viewport & Content Depth",
            "status": "strong" if (has_viewport and text_words >= 250) else "needs_work",
            "evidence": f"Mobile viewport: {'Present' if has_viewport else 'Missing'} · Homepage word count: {text_words} words.",
            "impact": "Thin pages (<250 words) struggle to rank for competitive service keywords.",
        })

        checks.append({
            "title": "Privacy Policy Compliance Link",
            "status": "strong" if has_privacy else "needs_work",
            "evidence": "Privacy policy link found in page." if has_privacy else "No Privacy Policy link detected.",
            "impact": "Required for running compliant Google Ads and Meta Lead Ads campaigns.",
        })

        return checks

    def _evaluate_social_presence(self, soup, html: str) -> list:
        checks = []
        platforms = {
            "Instagram": r"instagram\.com/",
            "TikTok": r"tiktok\.com/",
            "Facebook": r"facebook\.com/",
            "LinkedIn": r"linkedin\.com/",
            "YouTube": r"youtube\.com/|youtu\.be/",
            "X (Twitter)": r"(twitter\.com/|x\.com/)",
        }
        linked = [name for name, pat in platforms.items() if re.search(pat, html, re.I)]
        has_video = bool(re.search(r"(<video|youtube\.com/embed|vimeo\.com|tiktok\.com|watch video|reels)", html, re.I))
        has_og = bool(soup.find("meta", attrs={"property": "og:title"}) and soup.find("meta", attrs={"property": "og:image"}))

        checks.append({
            "title": "Social Profiles Linked on Website",
            "status": "strong" if len(linked) >= 2 else ("needs_work" if len(linked) == 1 else "priority"),
            "evidence": f"Linked profiles: {', '.join(linked)}." if linked else "No social media profile links found on homepage.",
            "impact": "Cross-linking social channels builds brand trust and warms up hesitant buyers.",
        })

        checks.append({
            "title": "Short-Form Video & Reels Presence",
            "status": "strong" if has_video else "needs_work",
            "evidence": "Video / Reels / TikTok content signals detected." if has_video else "No video or Reels embeds found on homepage.",
            "impact": "Video demonstrations dramatically increase time-on-site and booking confidence.",
        })

        checks.append({
            "title": "Social Share Cards (OpenGraph Tags)",
            "status": "strong" if has_og else "needs_work",
            "evidence": "OpenGraph title & preview image configured." if has_og else "Missing og:title or og:image tags for WhatsApp/social link previews.",
            "impact": "When prospects share your link on WhatsApp or Instagram, OpenGraph controls the preview card.",
        })

        return checks

    # -------------------------------------------------------------------------
    # AEO & AI Discoverability
    # -------------------------------------------------------------------------
    def _meta_content(self, soup, attr: str, key: str) -> str:
        tag = soup.find("meta", attrs={attr: re.compile(rf"^{re.escape(key)}$", re.I)})
        return (tag.get("content") or "").strip() if tag else ""

    def _iter_json_ld_nodes(self, data):
        """Yield every object in a JSON-LD payload, including @graph members."""
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                graph = node.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                elif isinstance(graph, dict):
                    stack.append(graph)

    def _extract_json_ld(self, soup) -> Tuple[int, int, List[str]]:
        valid_blocks = 0
        invalid_blocks = 0
        schema_types = set()

        for tag in soup.find_all("script", attrs={"type": re.compile(r"^application/ld\+json$", re.I)}):
            raw = (tag.string or tag.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                invalid_blocks += 1
                continue

            valid_blocks += 1
            for node in self._iter_json_ld_nodes(data):
                node_type = node.get("@type")
                if isinstance(node_type, str):
                    schema_types.add(node_type)
                elif isinstance(node_type, list):
                    schema_types.update(t for t in node_type if isinstance(t, str))

        return valid_blocks, invalid_blocks, sorted(schema_types)

    def _robots_blocks_bot(self, robots_text: str, bot: str) -> bool:
        """Return True when robots.txt disallows this bot from the audited path."""
        if not robots_text:
            return False

        groups: List[Tuple[List[str], List[Tuple[str, str]]]] = []
        agents: List[str] = []
        rules: List[Tuple[str, str]] = []

        for raw_line in robots_text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()

            if key == "user-agent":
                if rules:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif key in ("allow", "disallow") and agents:
                rules.append((key, value))
        if rules:
            groups.append((agents, rules))

        bot_key = bot.lower()
        specific = [group_rules for group_agents, group_rules in groups if bot_key in group_agents]
        wildcard = [group_rules for group_agents, group_rules in groups if "*" in group_agents]
        active_rules = specific[-1] if specific else (wildcard[-1] if wildcard else [])

        target_path = self.parsed_url.path or "/"
        blocked = False
        for key, value in active_rules:
            if not value:
                continue
            if value != "/" and not target_path.startswith(value.rstrip("*")):
                continue
            if key == "disallow":
                blocked = True
            elif key == "allow":
                blocked = False
        return blocked

    def _evaluate_aeo(self, soup, html: str, robots_text: str) -> list:
        checks = []

        # 1. JSON-LD schema and core entity recognition
        valid_blocks, invalid_blocks, schema_types = self._extract_json_ld(soup)
        identity_types = sorted(t for t in schema_types if t in CORE_ENTITY_SCHEMA_TYPES)
        has_faq = "FAQPage" in schema_types

        if valid_blocks and identity_types:
            schema_status = "strong"
        elif valid_blocks or invalid_blocks:
            schema_status = "needs_work"
        else:
            schema_status = "priority"

        if valid_blocks and identity_types:
            schema_evidence = (
                f"Valid JSON-LD found ({valid_blocks} block(s)). "
                f"Core entities: {', '.join(identity_types)}. "
                f"FAQPage: {'present' if has_faq else 'not found'}."
            )
        elif valid_blocks:
            schema_evidence = (
                f"Valid JSON-LD found ({valid_blocks} block(s)), but no Organization, "
                f"LocalBusiness or WebSite entity. Types: {', '.join(schema_types) or 'none'}."
            )
        elif invalid_blocks:
            schema_evidence = f"Found {invalid_blocks} JSON-LD block(s), but none parsed as valid JSON."
        else:
            schema_evidence = "No JSON-LD schema markup found."

        checks.append({
            "title": "JSON-LD Schema & Core Entity",
            "status": schema_status,
            "evidence": schema_evidence,
            "impact": "Answer engines use Organization, LocalBusiness and WebSite schema to verify who you are and cite your business.",
            "weight": 0.30,
        })

        # 2. OpenGraph and Twitter card metadata
        og_title = self._meta_content(soup, "property", "og:title")
        og_desc = self._meta_content(soup, "property", "og:description")
        og_image = self._meta_content(soup, "property", "og:image")
        twitter_card = self._meta_content(soup, "name", "twitter:card")
        og_count = sum(bool(v) for v in (og_title, og_desc, og_image))

        if og_count == 3:
            og_status = "strong"
        elif og_count:
            og_status = "needs_work"
        else:
            og_status = "priority"

        checks.append({
            "title": "OpenGraph & Twitter Card Metadata",
            "status": og_status,
            "evidence": (
                f"OpenGraph title: {'present' if og_title else 'missing'} · "
                f"description: {'present' if og_desc else 'missing'} · "
                f"image: {'present' if og_image else 'missing'} · "
                f"Twitter card: {'present' if twitter_card else 'missing'}."
            ),
            "impact": "Rich preview metadata helps search and AI surfaces render accurate titles, summaries and images.",
            "weight": 0.20,
        })

        # 3. Semantic heading hierarchy
        h1s = [h.get_text(strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
        h2s = [h.get_text(strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)]
        h3s = [h.get_text(strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)]

        if len(h1s) == 1 and (h2s or h3s):
            heading_status = "strong"
        elif len(h1s) >= 1:
            heading_status = "needs_work"
        else:
            heading_status = "priority"

        checks.append({
            "title": "Semantic Heading Hierarchy",
            "status": heading_status,
            "evidence": f"Found {len(h1s)} H1, {len(h2s)} H2 and {len(h3s)} H3 heading(s).",
            "impact": "A single H1 with supporting H2/H3 sections helps answer engines chunk and quote the right content.",
            "weight": 0.15,
        })

        # 4. Meta description for AI summaries
        meta_desc = self._meta_content(soup, "name", "description")
        desc_len = len(meta_desc)
        if 50 <= desc_len <= 165:
            desc_status = "strong"
        elif desc_len:
            desc_status = "needs_work"
        else:
            desc_status = "priority"

        checks.append({
            "title": "Meta Description for AI Summaries",
            "status": desc_status,
            "evidence": f"{desc_len} characters present." if desc_len else "No meta description tag found.",
            "impact": "Search snippets and AI summaries rely on a concise description of the page's core offer.",
            "weight": 0.15,
        })

        # 5. AI crawler access
        robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
        robots_meta_content = (robots_meta.get("content") or "").lower() if robots_meta else ""
        ai_meta_blocked = any(
            "noindex" in self._meta_content(soup, "name", bot).lower()
            for bot in AI_CRAWLER_BOTS
        )
        blocked_bots = [bot for bot in AI_CRAWLER_BOTS if self._robots_blocks_bot(robots_text, bot)]

        if "noindex" in robots_meta_content or ai_meta_blocked:
            crawler_status = "priority"
        elif len(blocked_bots) >= 3:
            crawler_status = "priority"
        elif blocked_bots:
            crawler_status = "needs_work"
        else:
            crawler_status = "strong"

        if "noindex" in robots_meta_content:
            crawler_evidence = "Homepage meta robots contains noindex, blocking AI crawlers."
        elif ai_meta_blocked:
            crawler_evidence = "A bot-specific meta robots tag blocks an AI crawler."
        elif blocked_bots:
            crawler_evidence = f"robots.txt blocks: {', '.join(blocked_bots)}."
        elif robots_text:
            crawler_evidence = "robots.txt does not block GPTBot, ClaudeBot, PerplexityBot, CCBot or Google-Extended."
        else:
            crawler_evidence = "No robots.txt found; AI crawlers are allowed by default."

        checks.append({
            "title": "AI Crawler Access (GPTBot, ClaudeBot, PerplexityBot)",
            "status": crawler_status,
            "evidence": crawler_evidence,
            "impact": "If AI crawlers are blocked, ChatGPT, Claude, Perplexity and Google AI cannot read or cite your site.",
            "weight": 0.20,
        })

        return checks

    # -------------------------------------------------------------------------
    # Scoring, Grading & Action Plan Helpers
    # -------------------------------------------------------------------------
    def _score_pillar(self, name: str, checks: list, weight: float) -> dict:
        if not checks:
            return {"name": name, "score": 0, "grade": "F", "weight": weight, "checks": []}
        points = 0.0
        possible = 0.0
        for c in checks:
            check_weight = float(c.get("weight", 1.0))
            possible += check_weight
            if c["status"] == "strong":
                points += check_weight
            elif c["status"] == "needs_work":
                points += check_weight * 0.5
        score = int(round((points / possible) * 100)) if possible else 0
        return {
            "name": name,
            "score": score,
            "grade": self._compute_grade(score),
            "weight": weight,
            "passed": sum(1 for c in checks if c["status"] == "strong"),
            "failed": sum(1 for c in checks if c["status"] != "strong"),
            "total": len(checks),
            "checks": checks,
        }

    def _compute_grade(self, score: float) -> str:
        if score >= 90:
            return "A+"
        if score >= 80:
            return "A"
        if score >= 70:
            return "B"
        if score >= 60:
            return "C"
        if score >= 50:
            return "D"
        return "F"

    def _build_action_plan(self, pillars: list) -> list:
        actions = []
        for p in pillars:
            for c in p["checks"]:
                if c["status"] in ("priority", "needs_work"):
                    actions.append({
                        "priority": "High Priority" if c["status"] == "priority" else "Quick Win",
                        "pillar": p["name"],
                        "title": c["title"],
                        "evidence": c["evidence"],
                        "impact": c.get("impact", ""),
                    })
        # Sort High Priority first
        actions.sort(key=lambda x: 0 if x["priority"] == "High Priority" else 1)
        return actions[:12]

    def _build_unreachable_report(self, error_msg: str) -> dict:
        return {
            "url": self.target_url,
            "business_name": self.business_name,
            "primary_city": self.primary_city,
            "industry": self.industry,
            "status": "failed",
            "error": f"Could not reach website: {error_msg}",
            "grade": "F",
            "scores": {"overall": 0, "passed_count": 0, "needs_work_count": 0, "priority_count": 1, "total_count": 1},
            "metrics": {},
            "competitors": [],
            "gbp_profile": {},
            "pillars": [],
            "aeo_score": 0,
            "aeo_checks": [],
            "action_plan": [],
            "findings": [{
                "pillar": "Connectivity",
                "check": "URL Reachability",
                "passed": False,
                "status": "priority",
                "details": f"Could not connect to {self.target_url}: {error_msg}",
            }],
        }
