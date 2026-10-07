from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl

from app.models.business_unit import RouteType


# ── Request Schemas ────────────────────────────────────────────────────────────

class BusinessUnitCreate(BaseModel):
    organization_id: str
    name: str = Field(..., min_length=2, max_length=255)
    route_type: RouteType = RouteType.EXPRESS
    webhook_url: Optional[str] = Field(None, max_length=512)
    owner_whatsapp_number: str = Field(..., min_length=10, max_length=20)
    owner_email: str = Field(..., max_length=255)


class BusinessUnitUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    route_type: Optional[RouteType] = None
    webhook_url: Optional[str] = Field(None, max_length=512)
    owner_whatsapp_number: Optional[str] = Field(None, min_length=10, max_length=20)
    owner_email: Optional[str] = Field(None, max_length=255)
    primary_url: Optional[str] = Field(None, max_length=512)
    social_media_links: Optional[str] = Field(None, max_length=1024)
    google_ads_customer_id: Optional[str] = Field(None, max_length=50)
    google_search_console_site: Optional[str] = Field(None, max_length=512)
    target_country: Optional[str] = Field(None, max_length=10)
    target_language: Optional[str] = Field(None, max_length=10)


# ── Response Schemas ───────────────────────────────────────────────────────────

class BusinessUnitResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    organization_id: str
    name: str
    route_type: RouteType
    webhook_url: Optional[str]
    owner_whatsapp_number: str
    owner_email: str
    primary_url: Optional[str] = None
    social_media_links: Optional[str] = None
    google_ads_customer_id: Optional[str] = None
    google_search_console_site: Optional[str] = None
    target_country: Optional[str] = "2404"
    target_language: Optional[str] = "1000"
    created_at: datetime


class BusinessUnitListResponse(BaseModel):
    model_config = {"from_attributes": True}

    total: int
    items: list[BusinessUnitResponse]
