from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.post import BlogPost, PostStatus
from app.schemas.post import (
    BlogPostCreate,
    BlogPostPaginatedResponse,
    BlogPostResponse,
    BlogPostUpdate,
    slugify,
)
from app.services.revalidation import trigger_nextjs_revalidation

logger = logging.getLogger("mugogo.blog")

# Router setup
public_router = APIRouter(tags=["Blog - Public"])
internal_router = APIRouter(prefix="/internal/blog", tags=["Blog - Internal Engine"])

security_bearer = HTTPBearer(auto_error=False)


async def verify_service_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> str:
    """Validate dedicated blog service token from header."""
    token = x_internal_token
    if not token and auth and auth.credentials:
        token = auth.credentials

    expected_token = settings.blog_service_token
    if not token or token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing dedicated blog service token.",
        )
    return token


from app.dependencies.auth import get_current_tenant
from app.models.organization import Organization

# ── Public Endpoints (Read-Only) ─────────────────────────────────────────────

@public_router.get(
    "/api/blog/posts",
    response_model=BlogPostPaginatedResponse,
    summary="Get paginated list of published posts",
)
@public_router.get(
    "/api/v1/blog/posts",
    response_model=BlogPostPaginatedResponse,
    include_in_schema=False,
)
@public_router.get(
    "/api/v1/blog/{org_id}/posts",
    response_model=BlogPostPaginatedResponse,
    summary="Get paginated list of published posts for client organization",
)
async def list_published_posts(
    org_id: Optional[str] = None,
    business_unit_id: Optional[str] = Query(None, description="Optional Business Unit ID filter"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    tenant: Optional[Organization] = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> Any:
    now_utc = datetime.now(timezone.utc)
    base_query = select(BlogPost).where(
        BlogPost.status == PostStatus.PUBLISHED,
        BlogPost.published_at <= now_utc,
    )

    target_org_id = tenant.id if tenant else org_id
    if target_org_id:
        base_query = base_query.where(BlogPost.organization_id == target_org_id)
    if business_unit_id:
        base_query = base_query.where(BlogPost.business_unit_id == business_unit_id)

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    offset = (page - 1) * limit
    posts_query = base_query.order_by(BlogPost.published_at.desc()).offset(offset).limit(limit)
    result = await db.execute(posts_query)
    posts = result.scalars().all()

    total_pages = (total + limit - 1) // limit if total > 0 else 1

    return {
        "posts": posts,
        "total": total,
        "page": page,
        "per_page": limit,
        "total_pages": total_pages,
    }


@public_router.get(
    "/api/blog/posts/recent",
    response_model=list[BlogPostResponse],
    summary="Get recent published posts for widgets",
)
@public_router.get(
    "/api/v1/blog/posts/recent",
    response_model=list[BlogPostResponse],
    include_in_schema=False,
)
async def list_recent_posts(
    limit: int = Query(5, ge=1, le=20, description="Number of posts to return"),
    org_id: Optional[str] = None,
    tenant: Optional[Organization] = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> Any:
    now_utc = datetime.now(timezone.utc)
    query = select(BlogPost).where(
        BlogPost.status == PostStatus.PUBLISHED,
        BlogPost.published_at <= now_utc,
    )
    target_org_id = tenant.id if tenant else org_id
    if target_org_id:
        query = query.where(BlogPost.organization_id == target_org_id)

    query = query.order_by(BlogPost.published_at.desc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@public_router.get(
    "/api/blog/posts/{slug}",
    response_model=BlogPostResponse,
    summary="Get single published post by slug",
)
@public_router.get(
    "/api/v1/blog/posts/{slug}",
    response_model=BlogPostResponse,
    include_in_schema=False,
)
@public_router.get(
    "/api/v1/blog/{org_id}/posts/{slug}",
    response_model=BlogPostResponse,
    summary="Get single published post by slug for client organization",
)
async def get_published_post_by_slug(
    slug: str,
    org_id: Optional[str] = None,
    tenant: Optional[Organization] = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
) -> Any:
    now_utc = datetime.now(timezone.utc)
    query = select(BlogPost).where(
        BlogPost.slug == slug.lower().strip(),
        BlogPost.status == PostStatus.PUBLISHED,
        BlogPost.published_at <= now_utc,
    )
    target_org_id = tenant.id if tenant else org_id
    if target_org_id:
        query = query.where(BlogPost.organization_id == target_org_id)

    result = await db.execute(query)
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Published blog post with slug '{slug}' not found.",
        )
    return post


# ── Internal Write / Auto-Publish Engine Endpoints ─────────────────────────

@internal_router.post(
    "/posts",
    response_model=BlogPostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update a blog post from content engine (Protected)",
)
async def create_or_upsert_post(
    payload: BlogPostCreate,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_service_token),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Ingest a new post from the backend content engine.
    Validation:
    - Slugs are URL-safe and unique.
    - Default status is 'draft' (enforcing human approval requirement).
    """
    target_slug = payload.slug or slugify(payload.title)
    if not target_slug:
        raise HTTPException(status_code=400, detail="Unable to compute valid slug from title.")

    # Check if slug exists
    query = select(BlogPost).where(BlogPost.slug == target_slug)
    existing = (await db.execute(query)).scalar_one_or_none()

    content_text = payload.content or payload.content_markdown or payload.content_html

    if existing:
        # Update existing draft/post
        existing.title = payload.title
        if content_text:
            existing.content_markdown = content_text
            existing.content_html = payload.content_html or content_text
        if payload.excerpt is not None:
            existing.excerpt = payload.excerpt
        if payload.featured_image_url is not None:
            existing.featured_image_url = payload.featured_image_url
        if payload.author_name is not None:
            existing.author_name = payload.author_name
        if payload.meta_title is not None:
            existing.meta_title = payload.meta_title
        if payload.meta_description is not None:
            existing.meta_description = payload.meta_description
        if payload.target_keywords is not None:
            existing.target_keywords = payload.target_keywords
        if payload.organization_id is not None:
            existing.organization_id = payload.organization_id
        if payload.business_unit_id is not None:
            existing.business_unit_id = payload.business_unit_id

        # Update status if provided
        prev_status = existing.status
        if payload.status:
            existing.status = payload.status
            if payload.status == PostStatus.PUBLISHED and not existing.published_at:
                existing.published_at = datetime.now(timezone.utc)

        existing.updated_at = datetime.now(timezone.utc)
        await db.flush()

        # If published, trigger ISR cache revalidation
        if existing.status == PostStatus.PUBLISHED:
            background_tasks.add_task(trigger_nextjs_revalidation, existing.slug)

        logger.info("[BLOG_ENGINE] Updated post slug=%s status=%s", existing.slug, existing.status)
        return existing

    # Create new post
    new_post = BlogPost(
        title=payload.title,
        slug=target_slug,
        excerpt=payload.excerpt,
        content_markdown=payload.content_markdown or content_text,
        content_html=payload.content_html or content_text,
        featured_image_url=payload.featured_image_url,
        author_name=payload.author_name or "Mugogo Team",
        status=payload.status,  # defaults to PostStatus.DRAFT
        published_at=payload.published_at
        if payload.status == PostStatus.PUBLISHED
        else (datetime.now(timezone.utc) if payload.status == PostStatus.PUBLISHED else None),
        meta_title=payload.meta_title,
        meta_description=payload.meta_description,
        target_keywords=payload.target_keywords,
        organization_id=payload.organization_id,
        business_unit_id=payload.business_unit_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db.add(new_post)
    await db.flush()

    if new_post.status == PostStatus.PUBLISHED:
        background_tasks.add_task(trigger_nextjs_revalidation, new_post.slug)

    logger.info("[BLOG_ENGINE] Ingested new post id=%s slug=%s status=%s", new_post.id, new_post.slug, new_post.status)
    return new_post


@internal_router.patch(
    "/posts/{post_id_or_slug}/publish",
    response_model=BlogPostResponse,
    summary="Human Approval endpoint to publish a draft post",
)
async def publish_post_human_approval(
    post_id_or_slug: str,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_service_token),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Human Approval Flip:
    Transitions a post from draft -> published and triggers Next.js revalidation webhook.
    """
    query = select(BlogPost).where(
        (BlogPost.id == post_id_or_slug) | (BlogPost.slug == post_id_or_slug.lower().strip())
    )
    result = await db.execute(query)
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Blog post not found.")

    post.status = PostStatus.PUBLISHED
    if not post.published_at:
        post.published_at = datetime.now(timezone.utc)
    post.updated_at = datetime.now(timezone.utc)

    await db.flush()

    # Trigger instant revalidation
    background_tasks.add_task(trigger_nextjs_revalidation, post.slug)

    logger.info("[HUMAN_APPROVAL] Approved & Published post id=%s slug=%s", post.id, post.slug)
    return post
