"""Add audit_leads table for the marketing audit lead magnet

Revision ID: 012_add_audit_leads
Revises: 011_invitations_active_org
Create Date: 2026-11-23

Context
-------
Creates the ``audit_leads`` table backing the public "Marketing Audit Lead
Magnet" feature. Each submission runs the multi-pillar website diagnostic
(Technical SEO, Tracking & Ads, Conversion Funnels, Crawlability/Local) and
persists the overall score plus the full pillar/check report as JSON.
"""

from alembic import op
from app.core.schema_sync import create_table_if_missing
import sqlalchemy as sa

revision = "012_add_audit_leads"
down_revision = "011_invitations_active_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "audit_leads",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("business_name", sa.String(length=255), nullable=False, index=True),
        sa.Column("target_url", sa.String(length=512), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, index=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("overall_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("report_data", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_leads")
