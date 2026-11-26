"""Add competitor_limit, search_radius_meters, latitude, longitude to business_units

Revision ID: 017_add_competitor_scope_params
Revises: 016_add_social_profiles
Create Date: 2026-11-27
"""

from alembic import op
from app.core.schema_sync import add_column_if_missing
import sqlalchemy as sa

revision = "017_add_competitor_scope_params"
down_revision = "016_add_social_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "business_units",
        sa.Column("competitor_limit", sa.Integer(), nullable=True, server_default="5"),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("search_radius_meters", sa.Integer(), nullable=True, server_default="10000"),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("latitude", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("longitude", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("business_units", "longitude")
    op.drop_column("business_units", "latitude")
    op.drop_column("business_units", "search_radius_meters")
    op.drop_column("business_units", "competitor_limit")