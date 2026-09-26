import pytest
import pytest_asyncio
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.config import settings
from app.core.database import Base, engine, AsyncSessionLocal
from app.models.post import BlogPost, PostStatus


@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_blog_internal_post_creation_and_human_approval():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        service_token = settings.blog_service_token

        # 1. Ingest post via internal engine — should default to DRAFT
        post_payload = {
            "title": "Tax Compliance in Kenya: Complete eTIMS Guide",
            "slug": "tax-compliance-kenya-etims-guide",
            "content": "# eTIMS Guide\nDetailed tax compliance instructions for businesses.",
            "excerpt": "Complete guide to eTIMS tax compliance in Kenya.",
            "author_name": "Mugogo Tax Team",
            "meta_title": "Tax Compliance Kenya eTIMS Guide 2026",
            "meta_description": "Learn everything about eTIMS compliance in Kenya.",
            "target_keywords": ["eTIMS Kenya", "tax compliance", "KRA eTIMS"],
        }

        resp = await client.post(
            "/api/internal/blog/posts",
            json=post_payload,
            headers={"X-Internal-Token": service_token},
        )

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["slug"] == "tax-compliance-kenya-etims-guide"
        assert data["status"] == "draft"  # Default is draft for human approval requirement
        assert data["published_at"] is None

        # 2. Public endpoint MUST NOT expose draft posts
        pub_resp = await client.get(f"/api/blog/posts/{data['slug']}")
        assert pub_resp.status_code == 404

        pub_list_resp = await client.get("/api/blog/posts")
        assert pub_list_resp.status_code == 200
        list_data = pub_list_resp.json()
        assert list_data["total"] == 0
        assert len(list_data["posts"]) == 0

        # 3. Human Approval Step — Flip status to PUBLISHED
        approve_resp = await client.patch(
            f"/api/internal/blog/posts/{data['id']}/publish",
            headers={"X-Internal-Token": service_token},
        )
        assert approve_resp.status_code == 200
        approved_data = approve_resp.json()
        assert approved_data["status"] == "published"
        assert approved_data["published_at"] is not None

        # 4. Now public endpoint MUST expose published post
        pub_post_resp = await client.get(f"/api/blog/posts/{data['slug']}")
        assert pub_post_resp.status_code == 200
        pub_data = pub_post_resp.json()
        assert pub_data["title"] == "Tax Compliance in Kenya: Complete eTIMS Guide"

        pub_list_resp2 = await client.get("/api/blog/posts")
        assert pub_list_resp2.status_code == 200
        assert pub_list_resp2.json()["total"] == 1


@pytest.mark.asyncio
async def test_blog_internal_auth_rejection():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid token
        resp = await client.post(
            "/api/internal/blog/posts",
            json={"title": "Test Title"},
            headers={"X-Internal-Token": "invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_blog_slug_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        service_token = settings.blog_service_token

        # Invalid slug with spaces or uppercase
        resp = await client.post(
            "/api/internal/blog/posts",
            json={
                "title": "Invalid Slug Test",
                "slug": "Invalid Slug With Spaces!",
            },
            headers={"X-Internal-Token": service_token},
        )
        assert resp.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_tenant_api_key_isolation():
    from app.models.organization import Organization, OrgStatus

    # Create two client organizations (e.g. Bistali vs Competitor)
    async with AsyncSessionLocal() as db:
        org_bistali = Organization(
            id="org-bistali-01",
            name="Bistali",
            api_key="mgg_live_bistali_secret_key_12345",
            status=OrgStatus.ACTIVE,
        )
        org_other = Organization(
            id="org-other-02",
            name="Other Client",
            api_key="mgg_live_other_secret_key_67890",
            status=OrgStatus.ACTIVE,
        )
        db.add_all([org_bistali, org_other])

        # Create published post for Bistali and published post for Other
        post_bistali = BlogPost(
            title="Bistali Exclusive Article",
            slug="bistali-exclusive-article",
            status=PostStatus.PUBLISHED,
            published_at=datetime.now(timezone.utc),
            organization_id=org_bistali.id,
        )
        post_other = BlogPost(
            title="Other Client Article",
            slug="other-client-article",
            status=PostStatus.PUBLISHED,
            published_at=datetime.now(timezone.utc),
            organization_id=org_other.id,
        )
        db.add_all([post_bistali, post_other])
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request posts using Bistali's X-API-Key header
        resp_bistali = await client.get(
            "/api/v1/blog/posts",
            headers={"X-API-Key": "mgg_live_bistali_secret_key_12345"},
        )
        assert resp_bistali.status_code == 200, resp_bistali.text
        data_bistali = resp_bistali.json()
        assert data_bistali["total"] == 1
        assert data_bistali["posts"][0]["slug"] == "bistali-exclusive-article"

        # Request posts using Other client's X-API-Key header
        resp_other = await client.get(
            "/api/v1/blog/posts",
            headers={"X-API-Key": "mgg_live_other_secret_key_67890"},
        )
        assert resp_other.status_code == 200, resp_other.text
        data_other = resp_other.json()
        assert data_other["total"] == 1
        assert data_other["posts"][0]["slug"] == "other-client-article"

