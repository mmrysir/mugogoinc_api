"""Add is_monthly_snapshot flag to audit_leads for two-tier scoring

Revision ID: 014_audit_monthly_snapshot
Revises: 013_audit_profile_tenant
Create Date: 2026-11-24

Context
-------
Adds a boolean ``is_monthly_snapshot`` column to ``audit_leads`` so audits can
be classified as live scans (False) or stable monthly benchmarks (True). The
Business Unit audit delta calculation prefers the most recent monthly snapshot
as its baseline, falling back to the first audit when no snapshot exists yet.
"""

from alembic import op
from app.core.schema_sync import add_column_if_missing
import sqlalchemy as sa

revision = "014_audit_monthly_snapshot"
down_revision = "013_audit_profile_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "audit_leads",
        sa.Column("is_monthly_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("audit_leads", "is_monthly_snapshot")
