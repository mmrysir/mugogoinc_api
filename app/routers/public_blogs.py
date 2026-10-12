from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.business_unit import BusinessUnit
from app.models.post import BlogPost, PostStatus
from app.schemas.post import PublicBlogFeedResponse, PublicBlogPostSchema

logger = logging.getLogger("mugogo.public_blogs")

router = APIRouter(prefix="/public/blogs", tags=["Public Blog Syndication"])


def apply_public_syndication_headers(response: Response) -> None:
    """Set open CORS and CDN cache-control headers for client website feeds."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"


@router.options("")
@router.options("/{slug}")
async def public_blogs_cors_options(response: Response) -> Response:
    """Handle CORS preflight requests from external client domains."""
    apply_public_syndication_headers(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=dict(response.headers))


@router.get(
    "",
    response_model=PublicBlogFeedResponse,
    summary="Get public syndicated blog feed for a business unit",
)
async def get_public_blog_feed(
    response: Response,
    business_unit_id: str = Query(..., description="Unique Business Unit ID"),
    limit: int = Query(10, ge=1, le=50, description="Items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Public, unauthenticated read endpoint for syndicating published blog posts 
    to client landing pages and external websites (Next.js, WordPress, Webflow, static HTML).
    """
    apply_public_syndication_headers(response)

    # 1. Verify business unit exists
    bu_query = select(BusinessUnit).where(BusinessUnit.id == business_unit_id)
    bu = (await db.execute(bu_query)).scalar_one_or_none()
    if not bu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business unit '{business_unit_id}' not found.",
        )

    now_utc = datetime.now(timezone.utc)

    # 2. Base query for published posts belonging to this BU
    base_query = select(BlogPost).where(
        BlogPost.business_unit_id == business_unit_id,
        BlogPost.status == PostStatus.PUBLISHED,
        BlogPost.published_at <= now_utc,
    )

    # 3. Optimized SQL COUNT
    count_query = select(func.count()).select_from(base_query.subquery())
    total_posts = (await db.execute(count_query)).scalar() or 0

    # 4. Paginate and order by newest first
    paginated_query = (
        base_query
        .order_by(BlogPost.published_at.desc())
        .offset(offset)
        .limit(limit)
    )
    posts = (await db.execute(paginated_query)).scalars().all()

    return PublicBlogFeedResponse(
        status="success",
        business_unit_id=business_unit_id,
        total_posts=total_posts,
        posts=posts,
    )


@router.get(
    "/{slug}",
    response_model=PublicBlogPostSchema,
    summary="Get single published blog post by slug for client website",
)
async def get_single_public_blog(
    slug: str,
    response: Response,
    business_unit_id: str = Query(..., description="Unique Business Unit ID"),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Fetch a single published article by slug for dedicated blog detail pages.
    """
    apply_public_syndication_headers(response)

    # 1. Verify business unit exists
    bu_query = select(BusinessUnit).where(BusinessUnit.id == business_unit_id)
    bu = (await db.execute(bu_query)).scalar_one_or_none()
    if not bu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business unit '{business_unit_id}' not found.",
        )

    now_utc = datetime.now(timezone.utc)
    post_query = select(BlogPost).where(
        BlogPost.business_unit_id == business_unit_id,
        BlogPost.slug == slug.lower().strip(),
        BlogPost.status == PostStatus.PUBLISHED,
        BlogPost.published_at <= now_utc,
    )
    post = (await db.execute(post_query)).scalar_one_or_none()

    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article not found or not published.",
        )

    return post
