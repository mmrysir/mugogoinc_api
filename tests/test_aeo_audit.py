"""Tests for the AEO & AI Discoverability audit pillar.

Covers:
  1. AEO check weighting keeps the pillar score normalised to 0-100.
  2. High-quality site (valid JSON-LD + core entities, OpenGraph, single H1,
     meta description, permissive robots) scores as a strong AEO pillar.
  3. Missing signals produce failing AEO checks and a low score.
  4. robots.txt AI crawler blocking is surfaced as a failing AEO check.
  5. The client summary exposes the AI discoverability metric.
"""

import pytest
from bs4 import BeautifulSoup

from app.services.audit_diagnostic import (
    AEO_PILLAR_NAME,
    AI_CRAWLER_BOTS,
    PILLAR_WEIGHTS,
    WebsiteAuditScanner,
)
from app.routers.business_unit_audits import _build_client_summary


FULL_YML = """
    <html>
      <head>
        <title>Safari Luxury</title>
        <meta name="description" content="Premium 5-day safari packages in Kenya with expert guides." />
        <meta property="og:title" content="Safari Luxury" />
        <meta property="og:description" content="Premium safaris in East Africa." />
        <meta property="og:image" content="https://example.com/og.jpg" />
        <meta name="twitter:card" content="summary_large_image" />
        <script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Safari Luxury"},{"@type":"WebSite","url":"https://example.com"}]}</script>
      </head>
      <body>
        <h1>Luxury Safaris</h1>
        <h2>Kenya</h2>
      </body>
    </html>
    """


def _make_scanner() -> WebsiteAuditScanner:
    return WebsiteAuditScanner(
        target_url="https://example.com",
        business_name="Safari Luxury",
        primary_city="Nairobi",
        industry="Hospitality",
    )


def _aeo_checks(scanner: WebsiteAuditScanner, html: str, robots_text: str = "") -> list:
    soup = BeautifulSoup(html, "html.parser")
    return scanner._evaluate_aeo(soup, html, robots_text)


class TestAeoPillar:
    def test_pillar_weights_sum_to_one(self):
        assert round(sum(PILLAR_WEIGHTS.values()), 2) == 1.0
        assert AEO_PILLAR_NAME in PILLAR_WEIGHTS
        assert PILLAR_WEIGHTS[AEO_PILLAR_NAME] == 0.12

    def test_high_quality_site_scores_strong(self):
        checks = _aeo_checks(_make_scanner(), FULL_YML, "User-agent: *\nAllow: /\n")
        assert len(checks) == 5
        assert [c["status"] for c in checks] == ["strong"] * 5
        aeo_score = _make_scanner()._score_pillar(
            AEO_PILLAR_NAME, checks, weight=PILLAR_WEIGHTS[AEO_PILLAR_NAME]
        )["score"]
        assert aeo_score == 100

    def test_missing_signals_produce_failing_checks(self):
        checks = _aeo_checks(_make_scanner(), "<html><head><title>x</title></head><body></body></html>", "")
        statuses = {c["title"]: c["status"] for c in checks}
        assert statuses["JSON-LD Schema & Core Entity"] == "priority"
        assert statuses["OpenGraph & Twitter Card Metadata"] == "priority"
        assert statuses["Semantic Heading Hierarchy"] == "priority"
        assert statuses["Meta Description for AI Summaries"] == "priority"
        assert statuses["AI Crawler Access (GPTBot, ClaudeBot, PerplexityBot)"] == "strong"

    def test_invalid_or_missing_schema_and_multiple_h1(self):
        html = """
        <html>
          <head>
            <script type="application/ld+json">not-json</script>
          </head>
          <body>
            <h1>One</h1>
            <h1>Two</h1>
          </body>
        </html>
        """
        checks = _aeo_checks(_make_scanner(), html, "")
        statuses = {c["title"]: c["status"] for c in checks}
        assert statuses["JSON-LD Schema & Core Entity"] == "needs_work"
        assert statuses["Semantic Heading Hierarchy"] == "needs_work"
        assert statuses["Meta Description for AI Summaries"] == "priority"
        assert statuses["OpenGraph & Twitter Card Metadata"] == "priority"

    def test_robots_txt_ai_crawler_blocking(self):
        scanner = _make_scanner()
        blocked_gptbot = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
        blocked_claudebot = "User-agent: ClaudeBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"

        checks = _aeo_checks(scanner, FULL_YML, blocked_gptbot)
        statuses = {c["title"]: c["status"] for c in checks}
        assert statuses["AI Crawler Access (GPTBot, ClaudeBot, PerplexityBot)"] == "needs_work"

        checks = _aeo_checks(scanner, FULL_YML, blocked_claudebot)
        statuses = {c["title"]: c["status"] for c in checks}
        assert statuses["AI Crawler Access (GPTBot, ClaudeBot, PerplexityBot)"] == "needs_work"

    def test_multiple_ai_crawlers_blocked_is_priority(self):
        robots = (
            "User-agent: GPTBot\nDisallow: /\n"
            "User-agent: ClaudeBot\nDisallow: /\n"
            "User-agent: PerplexityBot\nDisallow: /\n"
            "User-agent: *\nAllow: /\n"
        )
        checks = _aeo_checks(_make_scanner(), FULL_YML, robots)
        statuses = {c["title"]: c["status"] for c in checks}
        assert statuses["AI Crawler Access (GPTBot, ClaudeBot, PerplexityBot)"] == "priority"

    def test_aeo_action_plan_includes_aeo_pillar(self):
        checks = _aeo_checks(_make_scanner(), "<html><body></body></html>", "")
        aeo_pillar = _make_scanner()._score_pillar(
            AEO_PILLAR_NAME, checks, weight=PILLAR_WEIGHTS[AEO_PILLAR_NAME]
        )
        action_plan = _make_scanner()._build_action_plan([aeo_pillar])
        assert action_plan, "AEO failures should be surfaced in the action plan"
        assert action_plan[0]["pillar"] == AEO_PILLAR_NAME
        assert action_plan[0]["priority"] in ("High Priority", "Quick Win")

    def test_client_summary_exposes_ai_discoverability(self):
        summary = _build_client_summary(
            {
                "pillars": [
                    {"name": "Search Engine Optimization", "score": 80, "grade": "A"},
                    {"name": "Google Business Profile", "score": 70, "grade": "B"},
                    {"name": AEO_PILLAR_NAME, "score": 65, "grade": "C"},
                ],
                "scores": {"overall": 70.0},
                "grade": "B",
                "action_plan": [],
                "metrics": {},
            },
            None,
        )
        assert summary["ai_discoverability"] == 65
        assert round(summary["online_visibility"], 1) == 71.7

    def test_legacy_reports_without_aeo_fall_back_safely(self):
        summary = _build_client_summary(
            {
                "pillars": [
                    {"name": "Search Engine Optimization", "score": 80, "grade": "A"},
                    {"name": "Google Business Profile", "score": 70, "grade": "B"},
                ],
                "scores": {"overall": 75.0},
                "grade": "B",
                "action_plan": [],
                "metrics": {},
            },
            None,
        )
        assert summary["ai_discoverability"] == 0
        # Legacy reports averaged only SEO + GBP.
        assert round(summary["online_visibility"], 1) == 75.0

    def test_default_check_weights_are_unchanged(self):
        scanner = _make_scanner()
        checks = [
            {"title": "A", "status": "strong", "evidence": "ok"},
            {"title": "B", "status": "needs_work", "evidence": "ok"},
        ]
        pillar = scanner._score_pillar("Legacy", checks, weight=0.2)
        assert pillar["score"] == 75
        assert pillar["total"] == 2
        assert pillar["passed"] == 1
        assert pillar["failed"] == 1
