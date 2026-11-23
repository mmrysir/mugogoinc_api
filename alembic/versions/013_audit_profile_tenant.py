"""Add audit profile fields to business_units and tenant scoping to audit_leads

Revision ID: 013_audit_profile_tenant
Revises: 012_add_audit_leads
Create Date: 2026-11-23

Context
-------
Adds nullable audit-profile columns (website_url, city, industry, phone) to
business_units for pre-filled audits, and nullable tenant-scoping FKs
(organization_id, business_unit_id, triggered_by_user_id) to audit_leads
so public lead-gen scans keep working while BU audits are linked to their
tenant.
"""

from alembic import op
from app.core.schema_sync import (
    add_column_if_missing,
    create_index_if_missing,
)
import sqlalchemy as sa

revision = "013_audit_profile_tenant"
down_revision = "012_add_audit_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Business unit audit profile fields (additive, nullable)
    add_column_if_missing("business_units", sa.Column("website_url", sa.String(512), nullable=True))
    add_column_if_missing("business_units", sa.Column("city", sa.String(255), nullable=True))
    add_column_if_missing("business_units", sa.Column("industry", sa.String(255), nullable=True))
    add_column_if_missing("business_units", sa.Column("phone", sa.String(50), nullable=True))

    # 2. Tenant scoping on audit_leads (nullable)
    add_column_if_missing(
        "audit_leads",
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
    )
    add_column_if_missing(
        "audit_leads",
        sa.Column("business_unit_id", sa.String(36), sa.ForeignKey("business_units.id", ondelete="CASCADE"), nullable=True),
    )
    add_column_if_missing(
        "audit_leads",
        sa.Column("triggered_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    create_index_if_missing("ix_audit_leads_organization_id", "audit_leads", ["organization_id"])
    create_index_if_missing("ix_audit_leads_business_unit_id", "audit_leads", ["business_unit_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_leads_business_unit_id", table_name="audit_leads")
    op.drop_index("ix_audit_leads_organization_id", table_name="audit_leads")
    op.drop_column("audit_leads", "triggered_by_user_id")
    op.drop_column("audit_leads", "business_unit_id")
    op.drop_column("audit_leads", "organization_id")
    op.drop_column("business_units", "phone")
    op.drop_column("business_units", "industry")
    op.drop_column("business_units", "city")
    op.drop_column("business_units", "website_url")
