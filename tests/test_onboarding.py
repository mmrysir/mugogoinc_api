"""
Tests for Sprint 3: Onboarding Engine
- POST /api/v1/onboarding/analyze-entity

Tests cover:
  1. Successful full analysis (scraper + keywords + social discovery mocked)
  2. RBAC: unapproved/frozen agent blocked (403)
  3. RBAC: agent with no org assignment blocked (403)
  4. RBAC: admin bypasses org checks
  5. ClientMarketingChannel record is created/updated in DB
  6. Fallback seed keyword when scraping fails
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.database import Base, engine, AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.models.user import User, UserRole
from app.models.organization import Organization
from app.models.business_unit import BusinessUnit
from app.models.tenant_channels import ClientMarketingChannel, AgentOrgAssignment

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        admin = User(
            id="admin-onboard-1",
            email="admin@mugogoinc.com",
            name="Admin User",
            hashed_password=hash_password("adminpass"),
            role=UserRole.SUPER_ADMIN,
            is_approved=True,
            is_frozen=False,
        )
        manager = User(
            id="manager-onboard-1",
            email="manager@mugogoinc.com",
            name="Manager User",
            hashed_password=hash_password("managerpass"),
            role=UserRole.MANAGER,
            is_approved=True,
            is_frozen=False,
        )
        approved_agent = User(
            id="agent-onboard-approved",
            email="agent@mugogoinc.com",
            name="Approved Agent",
            hashed_password=hash_password("agentpass"),
            role=UserRole.AGENT,
            is_approved=True,
            is_frozen=False,
        )
        unapproved_agent = User(
            id="agent-onboard-unapproved",
            email="unagent@mugogoinc.com",
            name="Unapproved Agent",
            hashed_password=hash_password("agentpass"),
            role=UserRole.AGENT,
            is_approved=False,
            is_frozen=False,
        )
        org = Organization(
            id="org-onboard-1",
            name="Test Safari Co",
            assigned_agent_id=None,
        )
        db.add_all([admin, manager, approved_agent, unapproved_agent, org])
        await db.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Mocked service responses ──────────────────────────────────────────────────

MOCK_SCRAPE_SUCCESS = {
    "success": True,
    "url": "https://example.co.ke",
    "title": "Example Safari — Best Safaris in Kenya",
    "meta_description": "Book your dream safari today.",
    "headings": ["Kenya Safaris", "Masai Mara Tours", "Budget Packages"],
}

MOCK_SCRAPE_FAIL = {
    "success": False,
    "url": "https://unreachable.co.ke",
    "error": "Connection timeout",
}

MOCK_KEYWORDS = [
    {
        "keyword": "kenya safari",
        "monthly_search_volume": 9900,
        "competition": "HIGH",
        "low_top_of_page_bid_usd": 0.50,
        "high_top_of_page_bid_usd": 2.10,
        "opportunity_score": 72,
    }
]

MOCK_SOCIAL = {
    "brand_name": "Example Safari — Best Safaris in Kenya",
    "niche": "https://example.co.ke",
    "discovered_socials": {
        "instagram.com": "https://www.instagram.com/examplesafari/",
        "youtube.com": "https://www.youtube.com/@examplesafari",
    },
}


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_onboarding_full_analysis_as_admin():
    """
    Super Admin with a valid org ID should receive a full analysis response
    including website analysis, keyword data, and social insights.
    ClientMarketingChannel record must be created in DB.
    """
    token = create_access_token({"sub": "admin-onboard-1"})
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.services.onboarding.scrape_client_website", new=AsyncMock(return_value=MOCK_SCRAPE_SUCCESS)),
        patch("app.services.onboarding.keyword_service.fetch_keyword_ideas", new=AsyncMock(return_value=MOCK_KEYWORDS)),
        patch("app.services.onboarding.discover_social_presence", new=AsyncMock(return_value=MOCK_SOCIAL)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/onboarding/analyze-entity",
                json={"org_id": "org-onboard-1", "website_url": "https://example.co.ke", "customer_id": "123-456-7890"},
                headers=headers,
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"

    result = data["data"]
    # Website analysis
    assert result["website_analysis"]["success"] is True
    assert result["website_analysis"]["title"] == "Example Safari — Best Safaris in Kenya"

    # Keyword data
    assert result["keyword_opportunities_count"] == 1
    assert result["top_keywords"][0]["keyword"] == "kenya safari"
    assert result["seed_keywords_used"][0] == "Example Safari — Best Safaris in Kenya"

    # Social insights
    assert "instagram.com" in result["social_insights"]["discovered_socials"]
    assert "youtube.com" in result["social_insights"]["discovered_socials"]

    # DB record created
    async with AsyncSessionLocal() as db:
        channel = await db.scalar(
            select(ClientMarketingChannel).where(ClientMarketingChannel.org_id == "org-onboard-1")
        )
        assert channel is not None
        assert channel.website_url == "https://example.co.ke"
        props = channel.gsc_platform_properties or {}
        assert props.get("google_ads_customer_id") == "123-456-7890"
        assert "instagram.com" in props
        assert "youtube.com" in props


@pytest.mark.asyncio
async def test_onboarding_unapproved_agent_blocked():
    """
    An unapproved agent must receive 403, not 500 or 200.
    """
    token = create_access_token({"sub": "agent-onboard-unapproved"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/onboarding/analyze-entity",
            json={"org_id": "org-onboard-1", "website_url": "https://example.co.ke"},
            headers=headers,
        )

    assert resp.status_code == 403
    assert "approved" in resp.json()["detail"].lower() or "frozen" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_onboarding_approved_agent_without_assignment_blocked():
    """
    An approved agent with no AgentOrgAssignment for the target org must be 403.
    """
    token = create_access_token({"sub": "agent-onboard-approved"})
    headers = {"Authorization": f"Bearer {token}"}

    # No AgentOrgAssignment row seeded — agent has no access
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/onboarding/analyze-entity",
            json={"org_id": "org-onboard-1", "website_url": "https://example.co.ke"},
            headers=headers,
        )

    assert resp.status_code == 403
    assert "access" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_onboarding_approved_agent_with_assignment_allowed():
    """
    An approved agent explicitly assigned to the org via AgentOrgAssignment
    should be allowed through to the analysis.
    """
    # Grant the agent access
    async with AsyncSessionLocal() as db:
        assignment = AgentOrgAssignment(
            agent_id="agent-onboard-approved",
            org_id="org-onboard-1",
        )
        db.add(assignment)
        await db.commit()

    token = create_access_token({"sub": "agent-onboard-approved"})
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.services.onboarding.scrape_client_website", new=AsyncMock(return_value=MOCK_SCRAPE_SUCCESS)),
        patch("app.services.onboarding.keyword_service.fetch_keyword_ideas", new=AsyncMock(return_value=MOCK_KEYWORDS)),
        patch("app.services.onboarding.discover_social_presence", new=AsyncMock(return_value=MOCK_SOCIAL)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/onboarding/analyze-entity",
                json={"org_id": "org-onboard-1", "website_url": "https://example.co.ke"},
                headers=headers,
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_onboarding_fallback_seed_when_scrape_fails():
    """
    When scraping fails, the hostname of the website URL is used as the fallback
    seed keyword for Google Ads, instead of crashing.
    """
    token = create_access_token({"sub": "admin-onboard-1"})
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.services.onboarding.scrape_client_website", new=AsyncMock(return_value=MOCK_SCRAPE_FAIL)),
        patch("app.services.onboarding.keyword_service.fetch_keyword_ideas", new=AsyncMock(return_value=[])),
        patch("app.services.onboarding.discover_social_presence", new=AsyncMock(return_value={
            "brand_name": "unreachable.co.ke", "niche": "", "discovered_socials": {}
        })),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/onboarding/analyze-entity",
                json={"org_id": "org-onboard-1", "website_url": "https://unreachable.co.ke"},
                headers=headers,
            )

    assert resp.status_code == 200
    result = resp.json()["data"]
    assert result["website_analysis"]["success"] is False
    # Fallback: hostname used as seed
    assert result["seed_keywords_used"] == ["unreachable.co.ke"]
    # No keywords, but no crash
    assert result["keyword_opportunities_count"] == 0


@pytest.mark.asyncio
async def test_onboarding_updates_existing_channel_record():
    """
    A second onboarding call for the same org should UPDATE the existing
    ClientMarketingChannel record, not create a duplicate.
    """
    token = create_access_token({"sub": "admin-onboard-1"})
    headers = {"Authorization": f"Bearer {token}"}

    with (
        patch("app.services.onboarding.scrape_client_website", new=AsyncMock(return_value=MOCK_SCRAPE_SUCCESS)),
        patch("app.services.onboarding.keyword_service.fetch_keyword_ideas", new=AsyncMock(return_value=MOCK_KEYWORDS)),
        patch("app.services.onboarding.discover_social_presence", new=AsyncMock(return_value=MOCK_SOCIAL)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # First call — creates record
            await client.post(
                "/api/v1/onboarding/analyze-entity",
                json={"org_id": "org-onboard-1", "website_url": "https://example.co.ke"},
                headers=headers,
            )
            # Second call — must update, not duplicate
            resp2 = await client.post(
                "/api/v1/onboarding/analyze-entity",
                json={"org_id": "org-onboard-1", "website_url": "https://updated-example.co.ke"},
                headers=headers,
            )

    assert resp2.status_code == 200

    async with AsyncSessionLocal() as db:
        from sqlalchemy import func
        count = await db.scalar(
            select(func.count()).select_from(ClientMarketingChannel).where(
                ClientMarketingChannel.org_id == "org-onboard-1"
            )
        )
        assert count == 1, f"Expected 1 channel record, got {count} (duplicate created!)"

        channel = await db.scalar(
            select(ClientMarketingChannel).where(ClientMarketingChannel.org_id == "org-onboard-1")
        )
        assert channel.website_url == "https://updated-example.co.ke"


@pytest.mark.asyncio
async def test_onboarding_setup_full_tenant():
    """
    Manager or Admin can call POST /api/v1/onboarding/setup to atomically create
    Organization + BusinessUnit + ClientMarketingChannel + run initial analysis.
    """
    token = create_access_token({"sub": "admin-onboard-1"})
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "org_name": "Beach Holiday Tours",
        "org_slug": "beach-holiday-tours",
        "website_url": "https://beachholidaytours.com",
        "bu_name": "Beach Holiday Tours - Diani",
        "owner_whatsapp_number": "+254712345678",
        "owner_email": "director@beachholidaytours.com",
        "google_ads_customer_id": "456-789-0123",
    }

    with (
        patch("app.services.onboarding.scrape_client_website", new=AsyncMock(return_value=MOCK_SCRAPE_SUCCESS)),
        patch("app.services.onboarding.keyword_service.fetch_keyword_ideas", new=AsyncMock(return_value=MOCK_KEYWORDS)),
        patch("app.services.onboarding.discover_social_presence", new=AsyncMock(return_value=MOCK_SOCIAL)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/onboarding/setup",
                json=payload,
                headers=headers,
            )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "success"
    org_id = data["org_id"]
    bu_id = data["bu_id"]
    assert data["org_slug"] == "beach-holiday-tours"

    # Verify DB records
    async with AsyncSessionLocal() as db:
        # Organization created
        org = await db.scalar(select(Organization).where(Organization.id == org_id))
        assert org is not None
        assert org.name == "Beach Holiday Tours"
        assert org.slug == "beach-holiday-tours"

        # BusinessUnit created
        bu = await db.scalar(select(BusinessUnit).where(BusinessUnit.id == bu_id))
        assert bu is not None
        assert bu.organization_id == org_id
        assert bu.name == "Beach Holiday Tours - Diani"
        assert bu.owner_whatsapp_number == "+254712345678"
        assert bu.owner_email == "director@beachholidaytours.com"
        assert bu.primary_url == "https://beachholidaytours.com"
        assert bu.google_ads_customer_id == "456-789-0123"
        assert bu.google_search_console_site == "sc-domain:beachholidaytours.com"

        # Marketing Channel created
        channel = await db.scalar(select(ClientMarketingChannel).where(ClientMarketingChannel.org_id == org_id))
        assert channel is not None
        assert channel.website_url == "https://beachholidaytours.com"
        assert channel.gsc_domain_property == "sc-domain:beachholidaytours.com"


@pytest.mark.asyncio
async def test_onboarding_setup_duplicate_slug_rejected():
    """
    Submitting an onboarding setup with an existing slug must return 409 Conflict.
    """
    token = create_access_token({"sub": "manager-onboard-1"})
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "org_name": "Beach Holiday Tours 2",
        "org_slug": "unique-safari-slug",
        "website_url": "https://unique-safari.com",
        "owner_whatsapp_number": "+254700000000",
        "owner_email": "owner@safari.com",
    }

    with (
        patch("app.services.onboarding.scrape_client_website", new=AsyncMock(return_value=MOCK_SCRAPE_SUCCESS)),
        patch("app.services.onboarding.keyword_service.fetch_keyword_ideas", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/v1/onboarding/setup", json=payload, headers=headers)
            assert resp1.status_code == 201

            # Duplicate call with same slug
            resp2 = await client.post("/api/v1/onboarding/setup", json=payload, headers=headers)
            assert resp2.status_code == 409
            assert "already taken" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_onboarding_setup_agent_forbidden():
    """
    An agent calling POST /api/v1/onboarding/setup must receive 403 Forbidden.
    """
    token = create_access_token({"sub": "agent-onboard-approved"})
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "org_name": "Unauthorized Org",
        "website_url": "https://unauth.com",
        "owner_whatsapp_number": "+254711111111",
        "owner_email": "agent@unauth.com",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/onboarding/setup", json=payload, headers=headers)

    assert resp.status_code == 403
    assert "managers and super admins" in resp.json()["detail"].lower()

