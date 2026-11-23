from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class AuditRunRequest(BaseModel):
    """Payload for the public lead-magnet audit endpoint (v2)."""

    url: str = Field(..., min_length=3)
    business_name: str = Field(..., min_length=1)
    primary_city: Optional[str] = Field(default="")
    email: EmailStr = Field(..., min_length=3)
    industry: Optional[str] = Field(default="General Business")
    phone: Optional[str] = Field(default=None)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            cleaned = f"https://{cleaned}"
        return cleaned.rstrip("/")


class AuditRunResponse(BaseModel):
    id: int
    business_name: str
    target_url: str
    industry: Optional[str] = None
    overall_score: float = 0.0
    report_data: Dict[str, Any] = {}
    created_at: datetime

    class Config:
        from_attributes = True
