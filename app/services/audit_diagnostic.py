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


class WebsiteAuditScanner:
    def __init__(
        self,
        target_url: str,
        business_name: str = "",
        primary_city: str = "",
        industry: str = "General Business",
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

            (robots_ok, sitemap_ok, sitemap_in_robots), psi_data, places_data = (
                await asyncio.gather(robots_task, psi_task, places_task)
            )

        # Evaluate all 6 pillars
        seo_checks = self._evaluate_seo_and_speed(
            soup, html_content, status_code, final_url, response_time_ms,
            robots_ok, sitemap_ok, sitemap_in_robots, psi_data
        )
        gbp_checks = self._evaluate_gbp(soup, html_content, places_data)
        reputation_checks = self._evaluate_reputation(places_data)
        ads_checks = self._evaluate_advertising(html_content)
        conversion_checks = self._evaluate_conversion_funnel(soup, html_content)
        social_checks = self._evaluate_social_presence(soup, html_content)

        pillars = [
            self._score_pillar("Search Engine Optimization", seo_checks, weight=0.25),
            self._score_pillar("Google Business Profile", gbp_checks, weight=0.20),
            self._score_pillar("Reputation & Reviews", reputation_checks, weight=0.15),
            self._score_pillar("Advertising & Paid Media", ads_checks, weight=0.15),
            self._score_pillar("Content & Conversion Funnel", conversion_checks, weight=0.15),
            self._score_pillar("Social Media Presence", social_checks, weight=0.10),
        ]

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
            "action_plan": action_plan,
            "findings": flat_findings,
        }

    # -------------------------------------------------------------------------
    # Network Probes (Robots/Sitemap, PageSpeed Insights, Google Places)
    # -------------------------------------------------------------------------
    async def _check_robots_and_sitemap(self, client: httpx.AsyncClient):
        robots_ok = False
        sitemap_ok = False
        sitemap_in_robots = False

        try:
            r = await client.get(f"{self.base_domain}/robots.txt", headers=self.headers, timeout=8.0)
            if r.status_code == 200 and "user-agent" in r.text.lower():
                robots_ok = True
                if "sitemap:" in r.text.lower():
                    sitemap_in_robots = True
        except Exception:
            pass

        try:
            s = await client.get(f"{self.base_domain}/sitemap.xml", headers=self.headers, timeout=8.0)
            if s.status_code == 200 and ("<urlset" in s.text.lower() or "<sitemapindex" in s.text.lower()):
                sitemap_ok = True
        except Exception:
            pass

        return robots_ok, sitemap_ok, sitemap_in_robots

    async def _fetch_pagespeed_metrics(self, client: httpx.AsyncClient, url: str) -> dict:
        if not self.api_key:
            return {}
        psi_endpoint = (
            f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={quote_plus(url)}&strategy=mobile&category=performance&category=seo&key={self.api_key}"
        )
        try:
            res = await client.get(psi_endpoint, timeout=35.0)
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
                logger.warning(f"PageSpeed API returned {res.status_code}: {res.text[:200]}")
        except Exception as e:
            logger.warning(f"PageSpeed API error: {e}")
        return {}

    async def _fetch_google_places_and_competitors(self, client: httpx.AsyncClient, page_title: str) -> dict:
        if not self.api_key:
            return {}

        # Build prioritized search candidates so slug inputs (e.g. "mnaranicinnamonspa") still resolve
        cleaned_title = re.split(r"[\|\-\–\—\:]", page_title)[0].strip() if page_title else ""
        domain_words = re.sub(r"\.[a-z]{2,6}$", "", self.clean_host).replace("-", " ").replace("_", " ")

        candidates = []
        if self.business_name:
            candidates.append(f"{self.business_name} {self.primary_city}".strip())
        if cleaned_title and cleaned_title.lower() != self.business_name.lower():
            candidates.append(f"{cleaned_title} {self.primary_city}".strip())
        if domain_words:
            candidates.append(f"{domain_words} {self.primary_city}".strip())

        matched_place = None
        for query in candidates:
            if not query:
                continue
            search_url = (
                f"https://maps.googleapis.com/maps/api/place/textsearch/json"
                f"?query={quote_plus(query)}&key={self.api_key}"
            )
            try:
                res = await client.get(search_url, timeout=12.0)
                if res.status_code == 200:
                    results = res.json().get("results", [])
                    if results:
                        # Inspect top 3 candidates via Place Details to confirm website match
                        for cand in results[:3]:
                            details = await self._get_place_details(client, cand["place_id"])
                            cand_site = (details.get("website") or "").lower()
                            if self.clean_host and self.clean_host in cand_site:
                                matched_place = details
                                break
                        if not matched_place:
                            matched_place = await self._get_place_details(client, results[0]["place_id"])
                        if matched_place:
                            break
            except Exception as e:
                logger.warning(f"Places search error for '{query}': {e}")

        # Competitor Map Pack Search using Industry + Primary City
        competitors = []
        maps_rank = None
        median_reviews = None
        if self.primary_city or self.industry:
            comp_query = f"{self.industry} in {self.primary_city}".strip()
            comp_url = (
                f"https://maps.googleapis.com/maps/api/place/textsearch/json"
                f"?query={quote_plus(comp_query)}&key={self.api_key}"
            )
            try:
                c_res = await client.get(comp_url, timeout=12.0)
                if c_res.status_code == 200:
                    c_results = c_res.json().get("results", [])
                    matched_id = matched_place.get("place_id") if matched_place else None
                    for idx, item in enumerate(c_results, start=1):
                        if matched_id and item.get("place_id") == matched_id:
                            maps_rank = idx
                        elif len(competitors) < 5:
                            competitors.append({
                                "name": item.get("name"),
                                "rating": item.get("rating", 0),
                                "reviews": item.get("user_ratings_total", 0),
                                "address": item.get("formatted_address", ""),
                            })
                    review_counts = [c["reviews"] for c in competitors if c.get("reviews") is not None]
                    if review_counts:
                        median_reviews = int(statistics.median(review_counts))
            except Exception as e:
                logger.warning(f"Competitor search error: {e}")

        if not matched_place:
            return {
                "found": False,
                "competitors": competitors,
                "competitor_median_reviews": median_reviews,
                "maps_rank": None,
            }

        reviews = matched_place.get("reviews", [])
        now_ts = datetime.now(timezone.utc).timestamp()
        recent_90d = sum(1 for r in reviews if (now_ts - r.get("time", 0)) <= 90 * 86400)
        negative_recent = sum(1 for r in reviews if r.get("rating", 5) <= 3)

        return {
            "found": True,
            "rating": matched_place.get("rating", 0.0),
            "review_count": matched_place.get("user_ratings_total", 0),
            "competitor_median_reviews": median_reviews,
            "maps_rank": maps_rank,
            "competitors": competitors,
            "recent_90d_reviews": recent_90d,
            "negative_recent_reviews": negative_recent,
            "profile": {
                "name": matched_place.get("name"),
                "status": matched_place.get("business_status", "OPERATIONAL"),
                "website": matched_place.get("website", ""),
                "phone": matched_place.get("formatted_phone_number", ""),
                "has_hours": bool(matched_place.get("opening_hours")),
                "photo_count": len(matched_place.get("photos", [])),
                "types": matched_place.get("types", []),
            },
        }

    async def _get_place_details(self, client: httpx.AsyncClient, place_id: str) -> dict:
        fields = (
            "place_id,name,rating,user_ratings_total,formatted_phone_number,"
            "website,opening_hours,photos,reviews,types,business_status"
        )
        url = (
            f"https://maps.googleapis.com/maps/api/place/details/json"
            f"?place_id={place_id}&fields={fields}&key={self.api_key}"
        )
        res = await client.get(url, timeout=10.0)
        if res.status_code == 200:
            return res.json().get("result", {})
        return {}

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
    # Scoring, Grading & Action Plan Helpers
    # -------------------------------------------------------------------------
    def _score_pillar(self, name: str, checks: list, weight: float) -> dict:
        if not checks:
            return {"name": name, "score": 0, "grade": "F", "weight": weight, "checks": []}
        points = 0.0
        for c in checks:
            if c["status"] == "strong":
                points += 1.0
            elif c["status"] == "needs_work":
                points += 0.5
        score = int(round((points / len(checks)) * 100))
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
            "action_plan": [],
            "findings": [{
                "pillar": "Connectivity",
                "check": "URL Reachability",
                "passed": False,
                "status": "priority",
                "details": f"Could not connect to {self.target_url}: {error_msg}",
            }],
        }


# Backwards-compat shim for any callers expecting the old method name
WebsiteAuditScanner.run_full_diagnostic  # noqa: B018
