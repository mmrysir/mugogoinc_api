from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RouteType(str, enum.Enum):
    EXPRESS = "EXPRESS"      # Instant WhatsApp push
    ENTERPRISE = "ENTERPRISE"  # Signed webhook forward to external CRM


class BusinessUnit(Base):
    __tablename__ = "business_units"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    route_type: Mapped[RouteType] = mapped_column(
        Enum(RouteType, name="route_type"),
        default=RouteType.EXPRESS,
        nullable=False,
    )
    webhook_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    owner_whatsapp_number: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    social_media_links: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    google_ads_customer_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    google_search_console_site: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    target_country: Mapped[Optional[str]] = mapped_column(String(10), default="2404", nullable=True)
    target_language: Mapped[Optional[str]] = mapped_column(String(10), default="1000", nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="business_units"
    )
    leads: Mapped[list["Lead"]] = relationship(  # noqa: F821
        "Lead", back_populates="business_unit", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BusinessUnit id={self.id!r} name={self.name!r} route={self.route_type}>"
