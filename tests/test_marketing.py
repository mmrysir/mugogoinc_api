import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import Base, engine, AsyncSessionLocal
from app.core.security import create_access_token, hash_password
from app.models.user import User, UserRole


@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed Admin User
    async with AsyncSessionLocal() as session:
        user = User(
            id="user-admin-1",
            email="admin@mugogoinc.com",
            name="Admin User",
            hashed_password=hash_password("adminpass"),
            role=UserRole.SUPER_ADMIN,
        )
        session.add(user)
        await session.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_keyword_search_and_campaign_health():
    token = create_access_token({"sub": "user-admin-1", "role": "SUPER_ADMIN"})
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Keyword search endpoint
        # NOTE: The Keyword Planner API returns EXPANDED ideas (a superset of seeds),
        # so we assert >= the number of seeds, not an exact match.
        kw_resp = await client.post(
            "/api/v1/marketing/keywords/search",
            json={"seed_keywords": ["3 day masai mara safari", "tax compliance kenya"]},
            headers=headers,
        )
        assert kw_resp.status_code == 200, kw_resp.text
        kw_data = kw_resp.json()
        assert kw_data["count"] >= 2, f"Expected at least 2 keyword ideas, got {kw_data['count']}"
        assert len(kw_data["keywords"]) >= 2
        assert kw_data["count"] == len(kw_data["keywords"])
        # Validate shape of each returned keyword idea
        for kw in kw_data["keywords"]:
            assert "keyword" in kw
            assert "monthly_search_volume" in kw
            assert "competition" in kw
            assert "low_top_of_page_bid_usd" in kw
            assert "high_top_of_page_bid_usd" in kw
            assert "opportunity_score" in kw

        # 2. Campaign health monitor endpoint
        camp_resp = await client.post(
            "/api/v1/marketing/campaigns/check-health",
            json=[
                {
                    "campaign_name": "Kenya Safari Search Ads",
                    "daily_spend_usd": 150.0,
                    "daily_budget_usd": 150.0,
                    "leads_count": 2,
                    "owner_phone": "+254712345678",
                }
            ],
            headers=headers,
        )
        assert camp_resp.status_code == 200, camp_resp.text
        camp_data = camp_resp.json()
        assert camp_data["total_campaigns_checked"] == 1
        assert camp_data["alerts_count"] >= 1  # High CPL alert ($75 > threshold $15)
