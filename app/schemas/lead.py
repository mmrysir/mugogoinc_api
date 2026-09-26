from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError
from pydantic import model_validator

import phonenumbers

from app.models.lead import SourceChannel, DeliveryStatus, CrmForwardStatus


# ── Ingestion Request ─────────────────────────────────────────────────────────

class GeoMetadata(BaseModel):
    ip_address: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    user_agent: Optional[str] = None


class LeadIngestRequest(BaseModel):
    """Universal inbound payload for the ingestion endpoint."""

    event_type: str = "full_submission"
    bu_id: Optional[str] = None

    source_channel: SourceChannel = SourceChannel.LANDING_PAGE
    full_name: str = Field("", max_length=255)
    phone_number: str = Field(
        "",
        description="E.164 formatted phone number (e.g. +254712345678)",
    )
    email: Optional[str] = Field(None, max_length=255)
    payload_meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value store for UTM params and custom form fields",
    )
    geo_metadata: Optional[GeoMetadata] = None

    # Attribution fields
    gclid: Optional[str] = None
    fbclid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    user_agent: Optional[str] = None

    @model_validator(mode="before")
    def validate_phone(cls, values: dict) -> dict:
        if values.get("event_type") == "partial_abandoned":
            return values

        # Normalize and validate phone number to E.164
        phone = values.get("phone_number")
        if not phone:
            raise ValueError("phone_number is required for full submissions")
        try:
            parsed = phonenumbers.parse(phone, None)
        except phonenumbers.NumberParseException as exc:
            raise ValueError(f"Invalid phone number: {exc}")

        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Invalid phone number")

        values["phone_number"] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
        return values


# ── Response Schemas ──────────────────────────────────────────────────────────

class LeadResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    business_unit_id: str
    source_channel: SourceChannel
    full_name: str
    phone_number: str
    email: Optional[str]
    payload_meta: Dict[str, Any]
    delivery_status: DeliveryStatus
    crm_forward_status: CrmForwardStatus
    gclid: Optional[str] = None
    fbclid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    user_agent: Optional[str] = None
    dedupe_hash: str
    created_at: datetime


class LeadListResponse(BaseModel):
    model_config = {"from_attributes": True}

    total: int
    items: list[LeadResponse]


class IngestResponse(BaseModel):
    """Returned by POST /ingest/{business_unit_id}."""

    lead_id: Optional[str]
    is_duplicate: bool
    message: str
    event_type: str = "full_submission"
