from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, EmailStr, field_validator


class OnboardingSetupRequest(BaseModel):
    org_name: str = Field(..., min_length=2, max_length=255, description="Brand / Organization Name")
    org_slug: Optional[str] = Field(None, max_length=255, description="URL-safe slug")
    website_url: str = Field(..., description="Primary business website URL")
    bu_name: Optional[str] = Field(None, max_length=255, description="Default Business Unit Name")
    owner_whatsapp_number: str = Field(..., min_length=5, max_length=25, description="Owner WhatsApp in E.164 format (+254...)")
    owner_email: EmailStr = Field(..., description="Owner Email for lead notifications")
    google_ads_customer_id: Optional[str] = Field(None, max_length=50, description="Google Ads Customer ID override (e.g. 123-456-7890)")
    google_search_console_site: Optional[str] = Field(None, max_length=512, description="GSC verified property (e.g. sc-domain:brand.com or https://...)")
    social_media_links: Optional[str] = Field(None, max_length=2048, description="Social media links or handles")
    target_country: Optional[str] = Field("2404", max_length=10, description="Google Ads country criteria ID (default: 2404 Kenya)")
    target_language: Optional[str] = Field("1000", max_length=10, description="Google Ads language criteria ID (default: 1000 English)")

    @field_validator("owner_whatsapp_number")
    @classmethod
    def clean_whatsapp(cls, v: str) -> str:
        cleaned = v.strip().replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            if cleaned.startswith("254"):
                cleaned = f"+{cleaned}"
            elif cleaned.startswith("0") and len(cleaned) == 10:
                cleaned = f"+254{cleaned[1:]}"
            else:
                cleaned = f"+{cleaned}"
        return cleaned


class OnboardingSetupResponse(BaseModel):
    status: str
    message: str
    org_id: str
    org_slug: str
    bu_id: str
    data: Optional[Dict[str, Any]] = None
