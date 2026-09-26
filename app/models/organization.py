from __future__ import annotations

import enum
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OrgStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ONBOARDING = "ONBOARDING"


class Organization(Base):
    __tablename__ = "organizations"

    @staticmethod
    def generate_api_key() -> str:
        return f"mgg_live_{secrets.token_urlsafe(32)}"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
        default=lambda: Organization.generate_api_key(),
    )
    status: Mapped[OrgStatus] = mapped_column(
        Enum(OrgStatus, name="org_status"),
        default=OrgStatus.ONBOARDING,
        nullable=False,
    )
    created_by_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_agent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    business_units: Mapped[list["BusinessUnit"]] = relationship(  # noqa: F821
        "BusinessUnit", back_populates="organization", cascade="all, delete-orphan"
    )
    creator: Mapped[Optional["User"]] = relationship(  # noqa: F821
        "User", foreign_keys=[created_by_id], back_populates="created_organizations"
    )
    assigned_agent: Mapped[Optional["User"]] = relationship(  # noqa: F821
        "User", foreign_keys=[assigned_agent_id], back_populates="assigned_organizations"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id!r} name={self.name!r} status={self.status}>"
