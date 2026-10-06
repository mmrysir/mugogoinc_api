from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    MANAGER = "MANAGER"
    AGENT = "AGENT"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        default=UserRole.AGENT,
        nullable=False,
    )
    is_approved: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )
    is_frozen: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    created_organizations: Mapped[list["Organization"]] = relationship(  # noqa: F821
        "Organization",
        back_populates="creator",
        foreign_keys="[Organization.created_by_id]",
    )
    assigned_organizations: Mapped[list["Organization"]] = relationship(  # noqa: F821
        "Organization",
        back_populates="assigned_agent",
        foreign_keys="[Organization.assigned_agent_id]",
    )
    org_assignments: Mapped[list["AgentOrgAssignment"]] = relationship(  # noqa: F821
        "AgentOrgAssignment",
        back_populates="agent",
        foreign_keys="[AgentOrgAssignment.agent_id]",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r} role={self.role}>"
