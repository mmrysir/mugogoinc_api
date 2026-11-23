from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Request ───────────────────────────────────────────────────────────────────

class AuditRunRequest(BaseModel):
    """Payload for the public lead-magnet audit endpoint."""

    url: str = Field(..., min_length=1, max_length=512, description="Target website URL")
    business_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr = Field(..., max_length=255)
    industry: Optional[str] = Field(None, max_length=255)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            cleaned = f"https://{cleaned}"
        return cleaned.rstrip("/")


# ── Diagnostic Check / Pillar / Finding / Score ───────────────────────────────

class AuditCheck(BaseModel):
    name: str
    passed: bool = False
    detail: Optional[str] = None


class AuditPillar(BaseModel):
    name: str
    score: float = 0.0
    checks: List[AuditCheck] = []


class AuditFinding(BaseModel):
    pillar: str
    check: str
    passed: bool = False
    details: Optional[str] = None


class AuditScoreSummary(BaseModel):
    overall: float = 0.0
    passed_count: int = 0
    total_count: int = 0


# ── Response ───────────────────────────────────────────────────────────────────

class AuditReport(BaseModel):
    audit_id: int
    url: str
    business_name: str
    industry: Optional[str] = None
    overall_score: float = 0.0
    status: str = "completed"
    scores: AuditScoreSummary = Field(default_factory=AuditScoreSummary)
    findings: List[AuditFinding] = []
    pillars: List[AuditPillar] = []
    generated_at: datetime


class AuditLeadResponse(BaseModel):
    id: int
    business_name: str
    target_url: str
    email: str
    industry: Optional[str] = None
    overall_score: float = 0.0
    created_at: datetime


class AuditRunResponse(BaseModel):
    status: str = "completed"
    audit: AuditReport
    lead: AuditLeadResponse
