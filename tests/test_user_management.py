import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.database import Base, engine, AsyncSessionLocal
from app.core.security import hash_password
from app.models.user import User, UserRole


@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_freeze_unfreeze_and_delete_user():
    async with AsyncSessionLocal() as db:
        admin = User(
            id="admin-1",
            email="admin@mugogo.com",
            name="Admin User",
            hashed_password=hash_password("AdminPass123!"),
            role=UserRole.SUPER_ADMIN,
            is_approved=True,
            is_frozen=False,
        )
        agent = User(
            id="agent-1",
            email="agent@mugogo.com",
            name="Agent User",
            hashed_password=hash_password("AgentPass123!"),
            role=UserRole.AGENT,
            is_approved=True,
            is_frozen=False,
        )
        db.add_all([admin, agent])
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login as Admin
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@mugogo.com", "password": "AdminPass123!"},
        )
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Freeze Agent account
        freeze_resp = await client.patch("/api/v1/users/agent-1/freeze", headers=headers)
        assert freeze_resp.status_code == 200
        assert freeze_resp.json()["is_frozen"] is True

        # 3. Agent login should now fail with 403 ACCOUNT_FROZEN
        agent_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "agent@mugogo.com", "password": "AgentPass123!"},
        )
        assert agent_login.status_code == 403
        assert agent_login.json()["detail"] == "ACCOUNT_FROZEN"

        # 4. Unfreeze Agent account
        unfreeze_resp = await client.patch("/api/v1/users/agent-1/unfreeze", headers=headers)
        assert unfreeze_resp.status_code == 200
        assert unfreeze_resp.json()["is_frozen"] is False

        # 5. Agent login should succeed now
        agent_login2 = await client.post(
            "/api/v1/auth/login",
            json={"email": "agent@mugogo.com", "password": "AgentPass123!"},
        )
        assert agent_login2.status_code == 200

        # 6. Delete Agent account
        del_resp = await client.delete("/api/v1/users/agent-1", headers=headers)
        assert del_resp.status_code == 200

        async with AsyncSessionLocal() as db:
            deleted_user = await db.scalar(select(User).where(User.id == "agent-1"))
            assert deleted_user is None
