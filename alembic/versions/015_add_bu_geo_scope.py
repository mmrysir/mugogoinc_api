"""Add search_radius_meters and geographic_scope_type to business_units

Revision ID: 015_add_bu_geo_scope
Revises: 014_audit_monthly_snapshot
Create Date: 2026-11-26
"""

from alembic import op
from app.core.schema_sync import add_column_if_missing
import sqlalchemy as sa

revision = "015_add_bu_geo_scope"
down_revision = "014_audit_monthly_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "business_units",
        sa.Column("search_radius_meters", sa.Integer(), nullable=True, server_default="5000"),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("geographic_scope_type", sa.String(20), nullable=True, server_default="radius"),
    )


def downgrade() -> None:
    op.drop_column("business_units", "geographic_scope_type")
    op.drop_column("business_units", "search_radius_meters")