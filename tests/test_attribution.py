import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.config import settings
from app.core.database import Base, engine, AsyncSessionLocal
from app.models.organization import Organization, OrgStatus
from app.models.business_unit import BusinessUnit, RouteType
from app.models.lead import Lead


@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed an Organization and BusinessUnit for test ingestion
    async with AsyncSessionLocal() as session:
        org = Organization(id="org-test-101", name="Test Agency Org", status=OrgStatus.ACTIVE)
        bu = BusinessUnit(
            id="bu-test-101",
            organization_id=org.id,
            name="Safari Branch",
            route_type=RouteType.EXPRESS,
            owner_whatsapp_number="+254712345678",
            owner_email="owner@safari.com",
        )
        session.add_all([org, bu])
        await session.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_lead_ingest_with_attribution():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        worker_key = settings.cloudflare_worker_secret

        import time
        unique_phone = f"+254799{int(time.time()) % 1000000:06d}"

        payload = {
            "source_channel": "GOOGLE_ADS",
            "full_name": "Jane Doe Attribution",
            "phone_number": unique_phone,
            "email": "jane@example.com",
            "gclid": "test_gclid_123456",
            "fbclid": "test_fbclid_654321",
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_campaign": "safari_promo_2026",
            "payload_meta": {"custom_note": "Interested in 3-day Mara trip"},
        }

        resp = await client.post(
            "/api/v1/ingest/bu-test-101",
            json=payload,
            headers={"X-Mugogo-Api-Key": worker_key},
        )

        assert resp.status_code == 202, resp.text
        res_data = resp.json()
        assert res_data["is_duplicate"] is False
        lead_id = res_data["lead_id"]

        # Verify database record attribution
        async with AsyncSessionLocal() as session:
            lead = await session.scalar(select(Lead).where(Lead.id == lead_id))
            assert lead is not None
            assert lead.gclid == "test_gclid_123456"
            assert lead.fbclid == "test_fbclid_654321"
            assert lead.utm_source == "google"
            assert lead.utm_medium == "cpc"
            assert lead.utm_campaign == "safari_promo_2026"
