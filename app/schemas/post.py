from __future__ import annotations

import re
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator

from app.models.post import PostStatus


def slugify(text: str) -> str:
    """Convert text into a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


class BlogPostCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    slug: Optional[str] = Field(None, description="URL-safe unique slug. Auto-generated from title if omitted.")
    content: Optional[str] = Field(None, description="Markdown or HTML content")
    content_markdown: Optional[str] = None
    content_html: Optional[str] = None
    excerpt: Optional[str] = None
    featured_image_url: Optional[str] = None
    author_name: Optional[str] = "Mugogo Team"
    status: PostStatus = Field(default=PostStatus.DRAFT, description="Default status is draft (requires human approval)")
    published_at: Optional[datetime] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keywords: Optional[Any] = None
    organization_id: Optional[str] = None
    business_unit_id: Optional[str] = None

    @field_validator("slug", mode="before")
    @classmethod
    def validate_or_generate_slug(cls, v: Optional[str], info: Any) -> Optional[str]:
        if v:
            v_clean = v.lower().strip()
            if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v_clean):
                raise ValueError("Slug must contain only lowercase alphanumeric characters and hyphens.")
            return v_clean
        return v


class BlogPostUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = None
    content: Optional[str] = None
    content_markdown: Optional[str] = None
    content_html: Optional[str] = None
    excerpt: Optional[str] = None
    featured_image_url: Optional[str] = None
    author_name: Optional[str] = None
    status: Optional[PostStatus] = None
    published_at: Optional[datetime] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keywords: Optional[Any] = None
    organization_id: Optional[str] = None
    business_unit_id: Optional[str] = None

    @field_validator("slug", mode="before")
    @classmethod
    def validate_slug_format(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v_clean = v.lower().strip()
            if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v_clean):
                raise ValueError("Slug must contain only lowercase alphanumeric characters and hyphens.")
            return v_clean
        return v


class BlogPostResponse(BaseModel):
    id: str
    organization_id: Optional[str] = None
    business_unit_id: Optional[str] = None
    title: str
    slug: str
    excerpt: Optional[str] = None
    content_markdown: Optional[str] = None
    content_html: Optional[str] = None
    content: Optional[str] = None
    featured_image_url: Optional[str] = None
    author_name: Optional[str] = None
    status: PostStatus
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    target_keywords: Optional[Any] = None

    class Config:
        from_attributes = True


class BlogPostPaginatedResponse(BaseModel):
    posts: List[BlogPostResponse]
    total: int
    page: int
    per_page: int
    total_pages: int
