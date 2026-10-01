import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

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
async def test_super_admin_does_not_need_approval():
    async with AsyncSessionLocal() as db:
        admin_user = User(
            email="superadmin@mugogo.com",
            name="Super Admin",
            hashed_password=hash_password("SuperSecret123!"),
            role=UserRole.SUPER_ADMIN,
            is_approved=False,  # Unapproved initial state
        )
        agent_user = User(
            email="agent@mugogo.com",
            name="Field Agent",
            hashed_password=hash_password("AgentSecret123!"),
            role=UserRole.AGENT,
            is_approved=False,  # Unapproved initial state
        )
        db.add_all([admin_user, agent_user])
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Super Admin login attempt — should succeed and auto-approve
        admin_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "superadmin@mugogo.com", "password": "SuperSecret123!"},
        )
        assert admin_login.status_code == 200, admin_login.text
        assert "access_token" in admin_login.json()

        # 2. Agent login attempt — should fail with PENDING_APPROVAL
        agent_login = await client.post(
            "/api/v1/auth/login",
            json={"email": "agent@mugogo.com", "password": "AgentSecret123!"},
        )
        assert agent_login.status_code == 403
        assert agent_login.json()["detail"] == "PENDING_APPROVAL"
