from __future__ import annotations
from datetime import datetime, timezone
import uuid
from sqlalchemy import String, ForeignKey, DateTime, Text, JSON, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

class AgentOrgAssignment(Base):
    __tablename__ = "agent_org_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("agent_id", "org_id", name="uq_agent_org_assignment"),
    )

    # Relationships
    agent = relationship("User", foreign_keys=[agent_id], back_populates="org_assignments")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id])
    organization = relationship("Organization", back_populates="agent_assignments")


class ClientMarketingChannel(Base):
    __tablename__ = "client_marketing_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    # Core Metadata & Financial Thresholds
    website_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gsc_domain_property: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cpl_threshold_usd: Mapped[float | None] = mapped_column(nullable=True)
    monthly_ad_budget_usd: Mapped[float | None] = mapped_column(nullable=True)
    alert_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    # Unified JSON Storage for All Platforms & Encrypted Tokens
    encrypted_oauth_tokens: Mapped[str | None] = mapped_column(Text, nullable=True) # Fernet encrypted JSON containing credentials for google_ads, meta, etc.
    gsc_platform_properties: Mapped[dict | None] = mapped_column(JSON, nullable=True) # e.g. {"instagram": "@handle", "tiktok": "@handle"}
    
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationship
    organization = relationship("Organization", back_populates="marketing_channels")
