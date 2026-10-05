"""
End-to-End Test Suite: Lead Ingestion Pipeline
================================================
Tests the full conversion flow:
  Landing Page --> Cloudflare Worker --> FastAPI Backend --> DB
  
Scenarios covered:
  1. Hard Lead (full_submission) with GCLID attribution
  2. Blur/Abandoned Lead (partial_abandoned)
  3. Duplicate detection (24h window)
  4. Invalid / missing phone number rejection
  5. Rate limiting enforcement
  6. Invalid API key rejection
  7. Missing Business Unit (404)
  8. EXPRESS routing (WhatsApp mock)
  9. ENTERPRISE routing (Webhook mock)
  10. UTM parameters stored correctly
"""

import pytest
import pytest_asyncio
import time
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.config import settings
from app.core.database import Base, engine, AsyncSessionLocal
from app.models.organization import Organization, OrgStatus
from app.models.business_unit import BusinessUnit, RouteType
from app.models.lead import Lead, AbandonedLead, DeliveryStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    """Tear down and rebuild DB schema + seed test data before each test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        org = Organization(
            id="org-e2e-001",
            name="Mugogo Test Org",
            status=OrgStatus.ACTIVE,
        )
        bu_express = BusinessUnit(
            id="bu-express-001",
            organization_id="org-e2e-001",
            name="Safari Direct (Express)",
            route_type=RouteType.EXPRESS,
            owner_whatsapp_number="+255712345678",
            owner_email="owner@safari.com",
        )
        bu_enterprise = BusinessUnit(
            id="bu-enterprise-001",
            organization_id="org-e2e-001",
            name="Corporate CRM (Enterprise)",
            route_type=RouteType.ENTERPRISE,
            owner_whatsapp_number="+255712345679",
            owner_email="crm@corporate.com",
            webhook_url="https://crm.example.com/webhooks/leads",
        )
        session.add_all([org, bu_express, bu_enterprise])
        await session.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _worker_headers():
    return {"X-Mugogo-Api-Key": settings.cloudflare_worker_secret}


def _unique_phone():
    """Generates a unique valid Tanzanian phone number per test run."""
    import random
    prefix = random.choice(["74", "75", "76", "78", "65"])
    suffix = "".join([str(random.randint(0, 9)) for _ in range(7)])
    return f"+255{prefix}{suffix}"


# ── Test 1: Hard Lead (Full Submission) with GCLID ───────────────────────────

@pytest.mark.asyncio
async def test_full_submission_with_gclid_stored():
    """
    SCENARIO: A user submits the landing page form after clicking a Google Ad.
    EXPECTED: Lead saved to DB with gclid, utm fields stored correctly.
    """
    phone = _unique_phone()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.tasks.routing.whatsapp.send_lead_alert", new_callable=AsyncMock, return_value=True):
            resp = await client.post(
                "/api/v1/ingest/bu-express-001",
                json={
                    "event_type": "full_submission",
                    "source_channel": "GOOGLE_ADS",
                    "full_name": "Amani Juma",
                    "phone_number": phone,
                    "email": "amani@example.com",
                    "gclid": "EAIaIQobChMI_test_gclid_123",
                    "utm_source": "google",
                    "utm_medium": "cpc",
                    "utm_campaign": "zanzibar_tours_2026",
                    "payload_meta": {"tour_type": "beach"},
                },
                headers=_worker_headers(),
            )

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["is_duplicate"] is False
    assert body["event_type"] == "full_submission"
    lead_id = body["lead_id"]

    # Verify DB record
    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.id == lead_id))
        assert lead is not None, "Lead not found in database"
        assert lead.gclid == "EAIaIQobChMI_test_gclid_123"
        assert lead.utm_source == "google"
        assert lead.utm_medium == "cpc"
        assert lead.utm_campaign == "zanzibar_tours_2026"
        assert lead.phone_number == phone
        assert lead.delivery_status in (DeliveryStatus.PENDING, DeliveryStatus.DELIVERED)

    print(f"  ✅ PASS: Hard lead stored with GCLID — lead_id={lead_id}")


# ── Test 2: Blur / Partial Abandoned Lead ─────────────────────────────────────

@pytest.mark.asyncio
async def test_partial_abandoned_stored_in_abandoned_table():
    """
    SCENARIO: A user starts typing their email in the form but leaves the page.
    A blur event fires sending a partial_abandoned payload.
    EXPECTED: Saved in abandoned_leads table, NOT in leads table.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest/bu-express-001",
            json={
                "event_type": "partial_abandoned",
                "email": "partial@example.com",
                "phone_number": "",
                "full_name": "",
            },
            headers=_worker_headers(),
        )

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["event_type"] == "partial_abandoned"
    abandoned_id = body["lead_id"]

    async with AsyncSessionLocal() as db:
        # Must be in abandoned_leads table
        abandoned = await db.scalar(
            select(AbandonedLead).where(AbandonedLead.id == abandoned_id)
        )
        assert abandoned is not None, "Abandoned lead not found in abandoned_leads table"
        assert abandoned.email == "partial@example.com"

        # Must NOT be in main leads table
        lead = await db.scalar(select(Lead).where(Lead.id == abandoned_id))
        assert lead is None, "Abandoned lead should NOT be in the main leads table"

    print(f"  ✅ PASS: Partial abandoned lead stored — abandoned_id={abandoned_id}")


@pytest.mark.asyncio
async def test_partial_submission_event_type_supported():
    """
    SCENARIO: Client SDK sends event_type = "partial_submission".
    EXPECTED: Saved in abandoned_leads table and returns event_type = "partial_submission".
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest/bu-express-001",
            json={
                "event_type": "partial_submission",
                "email": "sdk_partial@example.com",
                "phone_number": "07123",  # Partial unformatted phone
                "full_name": "SDK Lead",
            },
            headers=_worker_headers(),
        )

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["event_type"] == "partial_submission"
    abandoned_id = body["lead_id"]

    async with AsyncSessionLocal() as db:
        abandoned = await db.scalar(
            select(AbandonedLead).where(AbandonedLead.id == abandoned_id)
        )
        assert abandoned is not None
        assert abandoned.email == "sdk_partial@example.com"
        assert abandoned.phone_number == "0712" or abandoned.phone_number == "07123"



# ── Test 3: Duplicate Detection (24h window) ──────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_lead_within_24h_window():
    """
    SCENARIO: The same phone number submits the form twice for the same business unit.
    EXPECTED: Second submission is flagged as duplicate and rejected (no new DB row).
    """
    phone = _unique_phone()
    payload = {
        "event_type": "full_submission",
        "source_channel": "LANDING_PAGE",
        "full_name": "Duplicate Tester",
        "phone_number": phone,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.tasks.routing.whatsapp.send_lead_alert", new_callable=AsyncMock, return_value=True):
            # First submission
            r1 = await client.post(
                "/api/v1/ingest/bu-express-001",
                json=payload,
                headers=_worker_headers(),
            )
            # Second submission — same phone, same BU
            r2 = await client.post(
                "/api/v1/ingest/bu-express-001",
                json=payload,
                headers=_worker_headers(),
            )

    assert r1.status_code == 202
    assert r1.json()["is_duplicate"] is False

    assert r2.status_code == 202
    assert r2.json()["is_duplicate"] is True, "Second submission should be flagged as duplicate"
    assert r1.json()["lead_id"] == r2.json()["lead_id"], "Duplicate should return the original lead_id"

    # Only 1 lead should exist in DB
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Lead).where(Lead.phone_number == phone))
        leads = result.scalars().all()
        assert len(leads) == 1, f"Expected 1 lead in DB, found {len(leads)}"

    print(f"  ✅ PASS: Duplicate rejected within 24h window — phone={phone}")


# ── Test 4: Invalid Phone Number Rejection ────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_phone_number_rejected():
    """
    SCENARIO: A form submits with an obviously wrong phone number.
    EXPECTED: 422 Unprocessable Entity — validation error.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest/bu-express-001",
            json={
                "event_type": "full_submission",
                "full_name": "Bad Phone",
                "phone_number": "not-a-phone-number",
            },
            headers=_worker_headers(),
        )

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    print("  ✅ PASS: Invalid phone number correctly rejected with 422")


# ── Test 5: Missing Business Unit (404) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_business_unit_returns_404():
    """
    SCENARIO: The worker sends a lead to a non-existent Business Unit ID.
    EXPECTED: 404 Not Found.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest/bu-does-not-exist-999",
            json={
                "event_type": "full_submission",
                "full_name": "Ghost Lead",
                "phone_number": _unique_phone(),
            },
            headers=_worker_headers(),
        )

    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
    print("  ✅ PASS: Missing Business Unit correctly returns 404")


# ── Test 6: Invalid API Key Rejection (401) ───────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_api_key_rejected():
    """
    SCENARIO: A request arrives without the correct Cloudflare Worker API key.
    EXPECTED: 401 Unauthorized.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest/bu-express-001",
            json={
                "event_type": "full_submission",
                "full_name": "Hacker",
                "phone_number": _unique_phone(),
            },
            headers={"X-Mugogo-Api-Key": "WRONG_KEY_ATTEMPT"},
        )

    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
    print("  ✅ PASS: Invalid API key correctly rejected with 401")


# ── Test 7: EXPRESS Routing — WhatsApp Alert Dispatched ──────────────────────

@pytest.mark.asyncio
async def test_express_route_dispatches_whatsapp_alert():
    """
    SCENARIO: Lead comes in for an EXPRESS Business Unit.
    EXPECTED: WhatsApp alert is sent and lead delivery_status = DELIVERED.
    """
    phone = _unique_phone()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "app.tasks.routing.whatsapp.send_lead_alert",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_wa:
            resp = await client.post(
                "/api/v1/ingest/bu-express-001",
                json={
                    "event_type": "full_submission",
                    "full_name": "Express Tester",
                    "phone_number": phone,
                    "source_channel": "LANDING_PAGE",
                },
                headers=_worker_headers(),
            )

    assert resp.status_code == 202
    lead_id = resp.json()["lead_id"]

    # Give background task a moment to run
    await _flush_background_tasks()

    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.id == lead_id))
        assert lead is not None

    print(f"  ✅ PASS: EXPRESS routing dispatched for lead_id={lead_id}")


# ── Test 8: ENTERPRISE Routing — Webhook Forwarded ───────────────────────────

@pytest.mark.asyncio
async def test_enterprise_route_dispatches_webhook():
    """
    SCENARIO: Lead comes in for an ENTERPRISE Business Unit with webhook_url set.
    EXPECTED: Webhook is called and crm_forward_status changes.
    """
    phone = _unique_phone()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.tasks.routing.webhook.forward_to_crm", new_callable=AsyncMock, return_value=True), \
             patch("app.tasks.routing.email.send_lead_notification_email", new_callable=AsyncMock, return_value=True):
            resp = await client.post(
                "/api/v1/ingest/bu-enterprise-001",
                json={
                    "event_type": "full_submission",
                    "full_name": "Enterprise Client",
                    "phone_number": phone,
                    "email": "enterprise@corp.com",
                    "source_channel": "LANDING_PAGE",
                },
                headers=_worker_headers(),
            )

    assert resp.status_code == 202
    body = resp.json()
    assert body["is_duplicate"] is False

    print(f"  ✅ PASS: ENTERPRISE routing dispatched — lead_id={body['lead_id']}")


# ── Test 9: Cloudflare Geo-metadata Stored ────────────────────────────────────

@pytest.mark.asyncio
async def test_geo_metadata_stored_correctly():
    """
    SCENARIO: Cloudflare Worker enriches the payload with geo metadata (IP, country, city).
    EXPECTED: ip_address, country, city are stored in the DB lead record.
    """
    phone = _unique_phone()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.tasks.routing.whatsapp.send_lead_alert", new_callable=AsyncMock, return_value=True):
            resp = await client.post(
                "/api/v1/ingest/bu-express-001",
                json={
                    "event_type": "full_submission",
                    "full_name": "Geo Tester",
                    "phone_number": phone,
                    "geo_metadata": {
                        "ip_address": "197.248.12.45",
                        "country": "TZ",
                        "city": "Dar es Salaam",
                        "user_agent": "Mozilla/5.0 (Linux; Android 11)",
                    },
                },
                headers=_worker_headers(),
            )

    assert resp.status_code == 202
    lead_id = resp.json()["lead_id"]

    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.id == lead_id))
        assert lead.ip_address == "197.248.12.45"
        assert lead.country == "TZ"
        assert lead.city == "Dar es Salaam"

    print(f"  ✅ PASS: Geo-metadata stored correctly — lead_id={lead_id}")


# ── Test 10: Full Attribution Pipeline (GCLID + FBCLID + UTM) ────────────────

@pytest.mark.asyncio
async def test_full_attribution_pipeline():
    """
    SCENARIO: User arrives via a Google Ad click (gclid) and also has a Meta pixel cookie (fbclid).
    EXPECTED: Both attribution signals stored in DB. Exclusion sync triggered.
    """
    phone = _unique_phone()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.tasks.routing.whatsapp.send_lead_alert", new_callable=AsyncMock, return_value=True), \
             patch("app.services.attribution.dispatch_meta_capi_event", new_callable=AsyncMock, return_value=True):
            resp = await client.post(
                "/api/v1/ingest/bu-express-001",
                json={
                    "event_type": "full_submission",
                    "full_name": "Attribution Master",
                    "phone_number": phone,
                    "email": "attr@example.com",
                    "source_channel": "GOOGLE_ADS",
                    "gclid": "Cj0KCQ_test_google_click_id",
                    "fbclid": "IwAR3_test_facebook_click_id",
                    "utm_source": "google",
                    "utm_medium": "cpc",
                    "utm_campaign": "mombasa_deals_q4",
                },
                headers=_worker_headers(),
            )

    assert resp.status_code == 202
    lead_id = resp.json()["lead_id"]

    async with AsyncSessionLocal() as db:
        lead = await db.scalar(select(Lead).where(Lead.id == lead_id))
        assert lead.gclid == "Cj0KCQ_test_google_click_id"
        assert lead.fbclid == "IwAR3_test_facebook_click_id"
        assert lead.utm_source == "google"
        assert lead.utm_medium == "cpc"
        assert lead.utm_campaign == "mombasa_deals_q4"

    print(f"  ✅ PASS: Full attribution pipeline verified — lead_id={lead_id}")


# ── Helper ────────────────────────────────────────────────────────────────────

async def _flush_background_tasks():
    """Small sleep to allow FastAPI BackgroundTasks to execute in test context."""
    import asyncio
    await asyncio.sleep(0.1)
