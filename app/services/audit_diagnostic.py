"""Automated website marketing diagnostic service.

Runs the multi-pillar digital marketing health scan used by the public
"Audit Lead Magnet" feature. Uses async HTTP (httpx) + HTML parsing
(BeautifulSoup) against a custom bot user agent.

Pillars:
  1. Technical SEO          — HTTP status, <title>, meta description
  2. Tracking & Ads         — GA4 / GTM / Google Ads / Meta Pixel tags
  3. Conversion Funnels     — <form> count, WhatsApp / tel: integration
  4. Crawlability (Local)  — robots.txt + sitemap.xml reachability
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("mugogo.audit")

BOT_USER_AGENT = "MugogoEngineBot/1.0"
REQUEST_TIMEOUT_SECONDS = 15.0

GA_PATTERN = re.compile(r"gtag\(|googletagmanager\.com|GTM-[A-Z0-9]+|G-[A-Z0-9]{6,}|AW-[A-Z0-9]+", re.IGNORECASE)
META_PIXEL_PATTERN = re.compile(r"fbq\(|connect\.facebook\.net", re.IGNORECASE)
WHATSAPP_PATTERN = re.compile(r"wa\.me|whatsapp\.com|href=[\"']tel:", re.IGNORECASE)


class WebsiteAuditScanner:
    """Async website scanner producing pillar-level marketing health scores."""

    async def _fetch(self, url: str) -> Tuple[Optional[int], str, str, Optional[str]]:
        """Fetch a page. Returns (status_code, html, final_url, error)."""
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": BOT_USER_AGENT},
            ) as client:
                resp = await client.get(url)
                return resp.status_code, resp.text or "", str(resp.url), None
        except Exception as exc:  # noqa: BLE001 - diagnostic must not crash the API
            logger.warning("[AUDIT] Fetch failed for %s: %s", url, exc)
            return None, "", url, str(exc)

    async def _fetch_probe(self, base_url: str, path: str) -> Tuple[Optional[int], str]:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": BOT_USER_AGENT},
            ) as client:
                resp = await client.get(urljoin(base_url, path))
                return resp.status_code, ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AUDIT] Probe failed for %s%s: %s", base_url, path, exc)
            return None, str(exc)

    # ── Pillar checks ──────────────────────────────────────────────────────────

    async def _check_technical_seo(self, url: str, status_code: Optional[int], html: str) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        checks.append(
            {
                "name": "http_status_200",
                "passed": status_code == 200,
                "detail": f"HTTP {status_code}" if status_code else "Request failed",
            }
        )
        soup = BeautifulSoup(html or "", "html.parser")
        title = soup.find("title")
        checks.append(
            {
                "name": "title_tag_present",
                "passed": bool(title and title.get_text(strip=True)),
                "detail": f"Title: {title.get_text(strip=True)[:60]}" if title else "No <title> tag",
            }
        )
        meta = soup.find("meta", attrs={"name": "description"})
        checks.append(
            {
                "name": "meta_description_present",
                "passed": bool(meta and (meta.get("content") or "").strip()),
                "detail": "Meta description found" if meta else "No meta description",
            }
        )
        score = round(100.0 * sum(1 for c in checks if c["passed"]) / len(checks), 2) if checks else 0.0
        return {"name": "Technical SEO", "score": score, "checks": checks}

    async def _check_tracking_ads(self, html: str) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = [
            {"name": "ga4_gtag_script", "passed": bool(GA_PATTERN.search(html or "")), "detail": "GA4/gtag/GTM detected"},
            {"name": "google_ads_tag", "passed": bool(GA_PATTERN.search(html or "")), "detail": "AW-/G- Google Ads tag detected"},
            {"name": "meta_pixel", "passed": bool(META_PIXEL_PATTERN.search(html or "")), "detail": "Meta Pixel (fbq) detected"},
        ]
        # GA4 / GTM / Google Ads all live under one combined tag check to stay accurate.
        checks = [
            {
                "name": "ga4_gtm_google_ads",
                "passed": bool(GA_PATTERN.search(html or "")),
                "detail": "GA4 / GTM / Google Ads tag found",
            },
            {
                "name": "meta_pixel",
                "passed": bool(META_PIXEL_PATTERN.search(html or "")),
                "detail": "Meta Pixel (fbq) found",
            },
        ]
        score = round(100.0 * sum(1 for c in checks if c["passed"]) / len(checks), 2) if checks else 0.0
        return {"name": "Tracking & Ads", "score": score, "checks": checks}

    async def _check_conversion_funnels(self, html: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html or "", "html.parser")
        forms = soup.find_all("form")
        wa = WHATSAPP_PATTERN.search(html or "")
        tel = 'href="tel:' in (html or "") or "href='tel:" in (html or "")
        checks: List[Dict[str, Any]] = [
            {
                "name": "contact_forms_present",
                "passed": len(forms) > 0,
                "detail": f"{len(forms)} <form> element(s) found",
            },
            {
                "name": "whatsapp_or_tel_contact",
                "passed": bool(wa or tel),
                "detail": "WhatsApp / direct tel: contact integration found" if (wa or tel) else "No WhatsApp / tel: contact channel",
            },
        ]
        score = round(100.0 * sum(1 for c in checks if c["passed"]) / len(checks), 2) if checks else 0.0
        return {"name": "Conversion Funnels", "score": score, "checks": checks}

    async def _check_crawlability(self, base_url: str) -> Dict[str, Any]:
        robots_status, _ = await self._fetch_probe(base_url, "/robots.txt")
        sitemap_status, _ = await self._fetch_probe(base_url, "/sitemap.xml")
        checks: List[Dict[str, Any]] = [
            {
                "name": "robots_txt_reachable",
                "passed": robots_status == 200,
                "detail": f"robots.txt → HTTP {robots_status}" if robots_status else "robots.txt unreachable",
            },
            {
                "name": "sitemap_xml_reachable",
                "passed": sitemap_status == 200,
                "detail": f"sitemap.xml → HTTP {sitemap_status}" if sitemap_status else "sitemap.xml unreachable",
            },
        ]
        score = round(100.0 * sum(1 for c in checks if c["passed"]) / len(checks), 2) if checks else 0.0
        return {"name": "Crawlability & Local Presence", "score": score, "checks": checks}

    # ── Full diagnostic ─────────────────────────────────────────────────────────

    async def run_full_diagnostic(self, url: str) -> Dict[str, Any]:
        status_code, html, final_url, fetch_error = await self._fetch(url)

        pillars: List[Dict[str, Any]] = []
        if status_code is None:
            # Page unreachable — surface a failed Technical SEO pillar.
            pillars.append(
                {
                    "name": "Technical SEO",
                    "score": 0.0,
                    "checks": [
                        {"name": "http_status_200", "passed": False, "detail": f"Fetch failed: {fetch_error}"},
                    ],
                }
            )
            pillars.append({"name": "Tracking & Ads", "score": 0.0, "checks": []})
            pillars.append({"name": "Conversion Funnels", "score": 0.0, "checks": []})
            pillars.append({"name": "Crawlability & Local Presence", "score": 0.0, "checks": []})
            overall = 0.0
            return {
                "url": url,
                "final_url": final_url,
                "status_code": None,
                "fetch_error": fetch_error,
                "pillars": pillars,
                "overall_score": 0.0,
            }

        pillars.append(await self._check_technical_seo(url, status_code, html))
        pillars.append(await self._check_tracking_ads(html))
        pillars.append(await self._check_conversion_funnels(html))
        pillars.append(await self._check_crawlability(final_url or url))

        pillar_scores = [float(p.get("score") or 0.0) for p in pillars]
        overall = round(sum(pillar_scores) / len(pillar_scores), 2) if pillar_scores else 0.0

        return {
            "url": url,
            "final_url": final_url,
            "status_code": status_code,
            "pillars": pillars,
            "overall_score": overall,
        }
