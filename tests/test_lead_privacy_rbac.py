import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.database import Base, engine, AsyncSessionLocal
from app.models.lead import Lead, DeliveryStatus, SourceChannel
from app.models.user import User, UserRole
from app.core.security import create_access_token


@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_lead_unblurred_for_super_admin():
    async with AsyncSessionLocal() as db:
        lead = Lead(
            id="test-lead-privacy-1",
            business_unit_id="bu-test-1",
            full_name="John Doe",
            phone_number="+254712345678",
            email="john.doe@gmail.com",
            source_channel=SourceChannel.LANDING_PAGE,
            delivery_status=DeliveryStatus.DELIVERED,
            dedupe_hash="test-privacy-hash-123",
            is_partial=False,
        )
        admin = User(
            id="admin-user-1",
            email="admin@mugogoinc.com",
            name="Admin User",
            hashed_password="hashedpassword",
            role=UserRole.SUPER_ADMIN,
            is_approved=True,
            is_frozen=False,
        )
        db.add_all([lead, admin])
        await db.commit()

    token = create_access_token({"sub": "admin-user-1"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/leads", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        item = [i for i in data["items"] if i["id"] == "test-lead-privacy-1"][0]
        assert item["is_blurred"] is False
        assert item["full_name"] == "John Doe"
        assert item["phone_number"] == "+254712345678"
        assert item["email"] == "john.doe@gmail.com"


@pytest.mark.asyncio
async def test_lead_blurred_for_unapproved_or_frozen_agent():
    async with AsyncSessionLocal() as db:
        lead = Lead(
            id="test-lead-privacy-2",
            business_unit_id="bu-test-1",
            full_name="John Doe",
            phone_number="+254712345678",
            email="john.doe@gmail.com",
            source_channel=SourceChannel.LANDING_PAGE,
            delivery_status=DeliveryStatus.DELIVERED,
            dedupe_hash="test-privacy-hash-456",
            is_partial=False,
        )
        unapproved_agent = User(
            id="agent-user-unapproved",
            email="unapproved@mugogoinc.com",
            name="Unapproved Agent",
            hashed_password="hashedpassword",
            role=UserRole.AGENT,
            is_approved=False,
            is_frozen=False,
        )
        db.add_all([lead, unapproved_agent])
        await db.commit()

    token = create_access_token({"sub": "agent-user-unapproved"})
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/leads", headers=headers)
        assert response.status_code == 200
        data = response.json()
        item = [i for i in data["items"] if i["id"] == "test-lead-privacy-2"][0]
        assert item["is_blurred"] is True
        assert item["full_name"] == "J*** D***"
        assert item["phone_number"] == "+254 XXX XXX 678"
        assert item["email"] == "j***e@gmail.com"

        # Also test single lead endpoint
        single_res = await ac.get("/api/v1/leads/test-lead-privacy-2", headers=headers)
        assert single_res.status_code == 200
        single_item = single_res.json()
        assert single_item["is_blurred"] is True
        assert single_item["full_name"] == "J*** D***"
