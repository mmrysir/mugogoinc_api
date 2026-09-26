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
    created_at: datetime


class BusinessUnitListResponse(BaseModel):
    model_config = {"from_attributes": True}

    total: int
    items: list[BusinessUnitResponse]
