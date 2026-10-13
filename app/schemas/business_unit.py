from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

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
    social_media_links: Optional[str] = Field(None, max_length=2048)
    google_ads_customer_id: Optional[str] = Field(None, max_length=50)
    google_search_console_site: Optional[str] = Field(None, max_length=512)
    google_business_place_id: Optional[str] = Field(None, max_length=255)
    google_business_map_url: Optional[str] = Field(None, max_length=1024)
    google_business_rating: Optional[float] = None
    google_business_review_count: Optional[int] = None
    digital_footprint_metrics: Optional[Union[Dict[str, Any], str]] = None
    target_country: Optional[str] = Field(None, max_length=10)
    target_language: Optional[str] = Field(None, max_length=10)
    # GSC OAuth fields (write-only; tokens are never returned in responses)
    gsc_site_url: Optional[str] = Field(None, max_length=512)


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
    google_business_place_id: Optional[str] = None
    google_business_map_url: Optional[str] = None
    google_business_rating: Optional[float] = None
    google_business_review_count: Optional[int] = None
    digital_footprint_metrics: Optional[Union[Dict[str, Any], str]] = None
    target_country: Optional[str] = "2404"
    target_language: Optional[str] = "1000"
    # GSC fields — raw tokens are NEVER exposed; only derived booleans / cached data
    gsc_site_url: Optional[str] = None
    gsc_connected: bool = False
    gsc_metrics: Optional[Dict[str, Any]] = None
    created_at: datetime

    @field_validator("digital_footprint_metrics", mode="before")
    @classmethod
    def parse_footprint(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().startswith("{"):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    @model_validator(mode="before")
    @classmethod
    def inject_gsc_connected(cls, data: Any) -> Any:
        """Derive gsc_connected from gsc_refresh_token before field mapping.

        Runs for both ORM objects (from_attributes mode) and plain dicts.
        Raw tokens are never passed to the client.
        """
        if hasattr(data, "gsc_refresh_token"):
            # ORM object: build a plain dict from all relevant attributes so
            # Pydantic can map them cleanly without from_attributes quirks.
            connected = bool(getattr(data, "gsc_refresh_token", None))
            return {
                "id": data.id,
                "organization_id": data.organization_id,
                "name": data.name,
                "route_type": data.route_type,
                "webhook_url": data.webhook_url,
                "owner_whatsapp_number": data.owner_whatsapp_number,
                "owner_email": data.owner_email,
                "primary_url": getattr(data, "primary_url", None),
                "social_media_links": getattr(data, "social_media_links", None),
                "google_ads_customer_id": getattr(data, "google_ads_customer_id", None),
                "google_search_console_site": getattr(data, "google_search_console_site", None),
                "google_business_place_id": getattr(data, "google_business_place_id", None),
                "google_business_map_url": getattr(data, "google_business_map_url", None),
                "google_business_rating": getattr(data, "google_business_rating", None),
                "google_business_review_count": getattr(data, "google_business_review_count", None),
                "digital_footprint_metrics": getattr(data, "digital_footprint_metrics", None),
                "target_country": getattr(data, "target_country", "2404"),
                "target_language": getattr(data, "target_language", "1000"),
                "gsc_site_url": getattr(data, "gsc_site_url", None),
                "gsc_connected": connected,
                "gsc_metrics": getattr(data, "gsc_metrics", None),
                "created_at": data.created_at,
            }
        # Dict path — just inject gsc_connected if missing
        if isinstance(data, dict) and "gsc_connected" not in data:
            data["gsc_connected"] = bool(data.get("gsc_refresh_token"))
        return data


class BusinessUnitListResponse(BaseModel):
    model_config = {"from_attributes": True}

    total: int
    items: list[BusinessUnitResponse]
