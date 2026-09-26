from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SourceChannel(str, enum.Enum):
    LANDING_PAGE = "LANDING_PAGE"
    META_LEAD_AD = "META_LEAD_AD"
    GOOGLE_ADS = "GOOGLE_ADS"
    TIKTOK = "TIKTOK"
    SMS = "SMS"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class CrmForwardStatus(str, enum.Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    QUEUED = "QUEUED"
    FORWARDED = "FORWARDED"
    FAILED = "FAILED"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_unit_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("business_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_channel: Mapped[SourceChannel] = mapped_column(
        Enum(SourceChannel, name="source_channel"),
        default=SourceChannel.LANDING_PAGE,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload_meta: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status"),
        default=DeliveryStatus.PENDING,
        nullable=False,
    )
    crm_forward_status: Mapped[CrmForwardStatus] = mapped_column(
        Enum(CrmForwardStatus, name="crm_forward_status"),
        default=CrmForwardStatus.NOT_REQUIRED,
        nullable=False,
    )
    # Geo/Behavioral & Attribution Data
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    gclid: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    fbclid: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    utm_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    utm_medium: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # Relationships
    business_unit: Mapped["BusinessUnit"] = relationship(  # noqa: F821
        "BusinessUnit", back_populates="leads"
    )

    def __repr__(self) -> str:
        return (
            f"<Lead id={self.id!r} name={self.full_name!r} "
            f"phone={self.phone_number!r} status={self.delivery_status}>"
        )


class AbandonedLead(Base):
    __tablename__ = "abandoned_leads"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    business_unit_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("business_units.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # Geo/Behavioral Data
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

