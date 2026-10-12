from __future__ import annotations

import re
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

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


class PublicBlogPostSchema(BaseModel):
    id: str
    business_unit_id: Optional[str] = None
    organization_id: Optional[str] = None
    title: str
    slug: str
    summary: Optional[str] = None
    excerpt: Optional[str] = None
    content: Optional[str] = None
    content_markdown: Optional[str] = None
    content_html: Optional[str] = None
    featured_image_url: Optional[str] = None
    target_keyword: Optional[str] = None
    target_keywords: Optional[Any] = None
    author_name: Optional[str] = "Editorial Team"
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True

    @model_validator(mode="before")
    @classmethod
    def populate_computed_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            summary = data.get("summary") or data.get("excerpt")
            content = data.get("content") or data.get("content_html") or data.get("content_markdown")
            kw = data.get("target_keyword")
            if not kw and data.get("target_keywords"):
                t_kw = data.get("target_keywords")
                if isinstance(t_kw, list) and t_kw:
                    kw = str(t_kw[0])
                elif isinstance(t_kw, dict) and t_kw:
                    kw = str(next(iter(t_kw.keys())))
                elif isinstance(t_kw, str):
                    kw = t_kw
            data["summary"] = summary
            data["excerpt"] = summary
            data["content"] = content
            if kw:
                data["target_keyword"] = kw
            return data

        excerpt = getattr(data, "excerpt", None)
        content_html = getattr(data, "content_html", None)
        content_markdown = getattr(data, "content_markdown", None)
        target_keywords = getattr(data, "target_keywords", None)
        target_keyword = None
        if isinstance(target_keywords, list) and target_keywords:
            target_keyword = str(target_keywords[0])
        elif isinstance(target_keywords, dict) and target_keywords:
            target_keyword = str(next(iter(target_keywords.keys())))
        elif isinstance(target_keywords, str):
            target_keyword = target_keywords

        return {
            "id": str(getattr(data, "id", "")),
            "business_unit_id": getattr(data, "business_unit_id", None),
            "organization_id": getattr(data, "organization_id", None),
            "title": getattr(data, "title", ""),
            "slug": getattr(data, "slug", ""),
            "summary": excerpt,
            "excerpt": excerpt,
            "content": content_html or content_markdown or "",
            "content_markdown": content_markdown,
            "content_html": content_html,
            "featured_image_url": getattr(data, "featured_image_url", None),
            "target_keyword": target_keyword,
            "target_keywords": target_keywords,
            "author_name": getattr(data, "author_name", "Editorial Team") or "Editorial Team",
            "published_at": getattr(data, "published_at", None),
        }


class PublicBlogFeedResponse(BaseModel):
    status: str = "success"
    business_unit_id: str
    total_posts: int
    posts: List[PublicBlogPostSchema]

