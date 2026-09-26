from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.organization import OrgStatus


# ── Request Schemas ────────────────────────────────────────────────────────────

class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    status: OrgStatus = OrgStatus.ONBOARDING
    assigned_agent_id: Optional[str] = Field(None, max_length=128)


class OrganizationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    status: Optional[OrgStatus] = None
    assigned_agent_id: Optional[str] = Field(None, max_length=128)


# ── Response Schemas ───────────────────────────────────────────────────────────

class OrganizationResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    status: OrgStatus
    assigned_agent_id: Optional[str]
    created_at: datetime


class OrganizationListResponse(BaseModel):
    model_config = {"from_attributes": True}

    total: int
    items: list[OrganizationResponse]
