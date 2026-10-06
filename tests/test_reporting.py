"""
Tests for WhatsApp Automated Reporting System
- GET  /api/v1/reports/weekly-preview
- POST /api/v1/reports/weekly-dispatch

Tests cover:
  1. Preview returns correct summary structure for a specific org
  2. Preview with no leads returns zero-value summary (no crash)
  3. Dispatch sends report and returns "dispatched" status
  4. Dispatch with mocked WA failure returns "failed" status
  5. RBAC: agents blocked from both endpoints
  6. Report scoping: org-specific report only counts that org's leads
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import Base, engine, AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.models.user import User, UserRole
from app.models.organization import Organization
from app.models.business_unit import BusinessUnit
from app.models.lead import Lead, DeliveryStatus, SourceChannel

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        admin = User(
            id="admin-report-1",
            email="admin@mugogoinc.com",
            name="Admin User",
            hashed_password=hash_password("adminpass"),
            role=UserRole.SUPER_ADMIN,
            is_approved=True,
            is_frozen=False,
        )
        manager = User(
            id="manager-report-1",
            email="manager@mugogoinc.com",
            name="Manager User",
            hashed_password=hash_password("managerpass"),
            role=UserRole.MANAGER,
            is_approved=True,
            is_frozen=False,
        )
        agent = User(
            id="agent-report-1",
            email="agent@mugogoinc.com",
            name="Agent User",
            hashed_password=hash_password("agentpass"),
            role=UserRole.AGENT,
            is_approved=True,
            is_frozen=False,
        )
        org_a = Organization(id="org-report-a", name="Safari Co")
        org_b = Organization(id="org-report-b", name="Tax Co")
        bu_a  = BusinessUnit(
            id="bu-report-a",
            organization_id="org-report-a",
            name="Safari BU",
            route_type="EXPRESS",
            owner_whatsapp_number="+254712345678",
            owner_email="owner@safari.co.ke",
        )

        # 3 leads for org_a this week
        now = datetime.now(timezone.utc)
        leads_a = [
            Lead(
                id=f"lead-report-a-{i}",
                business_unit_id="bu-report-a",
                full_name=f"Client {i}",
                phone_number=f"+25471234567{i}",
                source_channel=SourceChannel.LANDING_PAGE,
                delivery_status=DeliveryStatus.DELIVERED if i < 2 else DeliveryStatus.FAILED,
                dedupe_hash=f"report-hash-a-{i}",
                is_partial=False,
                created_at=now - timedelta(days=i),
            )
            for i in range(3)
        ]

        db.add_all([admin, manager, agent, org_a, org_b, bu_a] + leads_a)
        await db.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_weekly_preview_correct_summary():
    """
    Preview for org-report-a should return exactly 3 total leads,
    2 delivered, 1 failed, 0 returning, and the correct org name.
    """
    token = create_access_token({"sub": "admin-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/reports/weekly-preview",
            params={"org_id": "org-report-a"},
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "preview"
    assert data["org_name"] == "Safari Co"

    summary = data["summary"]
    assert summary["total_leads"] == 3
    assert summary["delivered"] == 2
    assert summary["failed"] == 1
    assert summary["returning"] == 0
    assert summary["delivery_rate_pct"] == round(2 / 3 * 100, 1)

    # Message must contain emoji header and org name
    assert "📊" in data["message"]
    assert "Safari Co" in data["message"]
    assert "LANDING PAGE" in data["message"].upper() or "Landing Page" in data["message"]


@pytest.mark.asyncio
async def test_weekly_preview_org_with_no_leads():
    """
    Preview for org-report-b (no leads seeded) should return all zeros,
    not crash or raise an exception.
    """
    token = create_access_token({"sub": "admin-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/reports/weekly-preview",
            params={"org_id": "org-report-b"},
            headers=headers,
        )

    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["total_leads"] == 0
    assert summary["delivered"] == 0
    assert summary["delivery_rate_pct"] == 0.0


@pytest.mark.asyncio
async def test_weekly_dispatch_sends_message():
    """
    Dispatch with a mocked WhatsApp send should return status "dispatched".
    """
    token = create_access_token({"sub": "admin-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.services.reporting.send_whatsapp_message", new=AsyncMock(return_value=True)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/reports/weekly-dispatch",
                json={"org_id": "org-report-a", "recipient_phone": "+254700000000"},
                headers=headers,
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "dispatched"
    assert "+254700000000" in data["message"]
    assert data["data"]["dispatched"] is True
    assert data["data"]["summary"]["total_leads"] == 3


@pytest.mark.asyncio
async def test_weekly_dispatch_returns_failed_when_wa_fails():
    """
    If WhatsApp send returns False, response status must be "failed" (not 500).
    """
    token = create_access_token({"sub": "admin-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.services.reporting.send_whatsapp_message", new=AsyncMock(return_value=False)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/reports/weekly-dispatch",
                json={"org_id": "org-report-a", "recipient_phone": "+254700000000"},
                headers=headers,
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert resp.json()["data"]["dispatched"] is False


@pytest.mark.asyncio
async def test_reporting_agent_blocked_from_preview():
    """
    Agents must be blocked from the preview endpoint with 403.
    """
    token = create_access_token({"sub": "agent-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/reports/weekly-preview",
            params={"org_id": "org-report-a"},
            headers=headers,
        )

    assert resp.status_code == 403
    assert "manager" in resp.json()["detail"].lower() or "admin" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reporting_agent_blocked_from_dispatch():
    """
    Agents must be blocked from the dispatch endpoint with 403.
    """
    token = create_access_token({"sub": "agent-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/reports/weekly-dispatch",
            json={"org_id": "org-report-a", "recipient_phone": "+254700000000"},
            headers=headers,
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_report_scoped_to_org():
    """
    Org-scoped report must ONLY count leads belonging to that org.
    org-report-b has no leads — its summary must be zero even though
    org-report-a has 3 leads in the same DB.
    """
    token = create_access_token({"sub": "admin-report-1"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_a = await client.get("/api/v1/reports/weekly-preview", params={"org_id": "org-report-a"}, headers=headers)
        resp_b = await client.get("/api/v1/reports/weekly-preview", params={"org_id": "org-report-b"}, headers=headers)

    assert resp_a.json()["summary"]["total_leads"] == 3
    assert resp_b.json()["summary"]["total_leads"] == 0
