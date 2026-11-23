"""Automated website marketing diagnostic service.

Runs the multi-pillar digital marketing health scan used by the public
"Audit Lead Magnet" feature.

Pillars:
  1. Technical SEO          — HTTP status, <title>, meta description
  2. Tracking & Ads         — GA / GTM / UA / Google Ads / Meta Pixel
  3. Conversion Funnels     — <form> count, WhatsApp / tel: integration
  4. Robots & Sitemap       — robots.txt + sitemap.xml reachability
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("mugogo.audit")


class WebsiteAuditScanner:
    def __init__(self, target_url: str):
        if not target_url.startswith("http://") and not target_url.startswith("https://"):
            self.target_url = f"https://{target_url}"
        else:
            self.target_url = target_url

        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

    async def run_full_diagnostic(self) -> dict:
        findings: List[Dict[str, Any]] = []
        html_content = ""
        status_code = 0

        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            try:
                response = await client.get(self.target_url, headers=self.headers)
                html_content = response.text
                status_code = response.status_code
            except Exception as e:  # noqa: BLE001 - defensive: never crash the API
                logger.error(f"Audit fetch error for {self.target_url}: {str(e)}")
                return {
                    "url": self.target_url,
                    "status": "failed",
                    "error": f"Failed to reach target URL: {str(e)}",
                    "scores": {"overall": 0, "passed_count": 0, "total_count": 0},
                    "findings": [
                        {
                            "pillar": "Connectivity",
                            "check": "URL Reachability",
                            "passed": False,
                            "details": f"Could not connect to target URL: {str(e)}",
                        }
                    ],
                }

            # 1. Technical SEO checks
            findings.extend(self._check_seo_tags(html_content, status_code))

            # 2. Tracking & Pixels checks
            findings.extend(self._check_tracking_pixels(html_content))

            # 3. Conversion Funnel checks
            findings.extend(self._check_conversion_signals(html_content))

            # 4. Robots & Sitemap probe
            base_domain = self._get_base_domain(self.target_url)
            robots_ok, sitemap_ok = await self._check_robots_and_sitemap(client, base_domain)
            findings.append(
                {
                    "pillar": "Technical SEO",
                    "check": "Robots.txt & Sitemap",
                    "passed": robots_ok or sitemap_ok,
                    "details": (
                        f"Robots.txt: {'Found' if robots_ok else 'Missing'}, "
                        f"Sitemap: {'Found' if sitemap_ok else 'Missing'}"
                    ),
                }
            )

        # Calculate overall score safely
        total_checks = len(findings)
        passed_checks = sum(1 for f in findings if f.get("passed", False))
        overall_score = round((passed_checks / total_checks) * 100, 2) if total_checks > 0 else 0.0

        return {
            "url": self.target_url,
            "status": "success",
            "scores": {
                "overall": overall_score,
                "passed_count": passed_checks,
                "total_count": total_checks,
            },
            "findings": findings,
        }

    def _check_seo_tags(self, html: str, status_code: int) -> list:
        soup = BeautifulSoup(html or "", "html.parser")
        findings = []

        findings.append(
            {
                "pillar": "Technical SEO",
                "check": "HTTP Status",
                "passed": 200 <= status_code < 400,
                "details": f"Response status code is {status_code}",
            }
        )

        title = soup.find("title")
        has_title = bool(title and title.text.strip())
        findings.append(
            {
                "pillar": "Technical SEO",
                "check": "Page Title",
                "passed": has_title,
                "details": f"Title found: '{title.text.strip()[:50]}...'" if has_title else "Missing or empty page title.",
            }
        )

        meta_desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        has_desc = bool(meta_desc and meta_desc.get("content"))
        findings.append(
            {
                "pillar": "Technical SEO",
                "check": "Meta Description",
                "passed": has_desc,
                "details": "Meta description is present." if has_desc else "Missing meta description tag.",
            }
        )

        return findings

    def _check_tracking_pixels(self, html: str) -> list:
        findings = []
        has_ga = bool(re.search(r"(gtag\(|GTM-|UA-|googletagmanager\.com)", html or "", re.I))
        findings.append(
            {
                "pillar": "Tracking & Ads",
                "check": "Google Analytics / Tag Manager",
                "passed": has_ga,
                "details": "Google tracking scripts detected." if has_ga else "No Google Analytics or GTM tags found.",
            }
        )

        has_meta_pixel = bool(re.search(r"(fbq\(|connect\.facebook\.net)", html or "", re.I))
        findings.append(
            {
                "pillar": "Tracking & Ads",
                "check": "Meta Pixel",
                "passed": has_meta_pixel,
                "details": "Meta Pixel detected." if has_meta_pixel else "No Meta Pixel found on page.",
            }
        )

        has_gads = bool(re.search(r"AW-", html or ""))
        findings.append(
            {
                "pillar": "Tracking & Ads",
                "check": "Google Ads Conversion Tag",
                "passed": has_gads,
                "details": "Google Ads conversion tagging detected." if has_gads else "Google Ads tag not detected.",
            }
        )

        return findings

    def _check_conversion_signals(self, html: str) -> list:
        soup = BeautifulSoup(html or "", "html.parser")
        findings = []

        forms = soup.find_all("form")
        has_forms = len(forms) > 0
        findings.append(
            {
                "pillar": "Conversion Funnels",
                "check": "Lead Capture Forms",
                "passed": has_forms,
                "details": f"Found {len(forms)} form(s) on landing page." if has_forms else "No lead capture forms detected.",
            }
        )

        has_whatsapp = bool(re.search(r"(wa\.me|whatsapp\.com)", html or "", re.I))
        findings.append(
            {
                "pillar": "Conversion Funnels",
                "check": "Instant Messaging (WhatsApp)",
                "passed": has_whatsapp,
                "details": "WhatsApp click-to-chat link detected." if has_whatsapp else "No instant WhatsApp link found.",
            }
        )

        return findings

    async def _check_robots_and_sitemap(
        self, client: httpx.AsyncClient, base_domain: str
    ) -> Tuple[bool, bool]:
        try:
            robots_resp = await client.get(f"{base_domain}/robots.txt", timeout=5.0)
            robots_ok = robots_resp.status_code == 200
        except Exception:  # noqa: BLE001
            robots_ok = False

        try:
            sitemap_resp = await client.get(f"{base_domain}/sitemap.xml", timeout=5.0)
            sitemap_ok = sitemap_resp.status_code == 200
        except Exception:  # noqa: BLE001
            sitemap_ok = False

        return robots_ok, sitemap_ok

    def _get_base_domain(self, url: str) -> str:
        parsed = httpx.URL(url)
        return f"{parsed.scheme}://{parsed.host}"
