"""Add social_profiles JSONB column to business_units

Revision ID: 016_add_social_profiles
Revises: 015_add_bu_geo_scope
Create Date: 2026-11-27
"""

from alembic import op
from app.core.schema_sync import add_column_if_missing
import sqlalchemy as sa

revision = "016_add_social_profiles"
down_revision = "015_add_bu_geo_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "business_units",
        sa.Column("social_profiles", sa.JSON(), nullable=True, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("business_units", "social_profiles")