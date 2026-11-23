from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLead(Base):
    """Lead-magnet audit submission + full diagnostic report storage.

    Tenant-scoped fields are nullable so public unauthenticated /audit
    lead-gen scans continue working seamlessly.
    """

    __tablename__ = "audit_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_url: Mapped[str] = mapped_column(String(512), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    report_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Tenant scoping (nullable for public lead-gen scans)
    organization_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    business_unit_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("business_units.id", ondelete="CASCADE"), nullable=True, index=True
    )
    triggered_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<AuditLead id={self.id} business_name={self.business_name!r}>"
