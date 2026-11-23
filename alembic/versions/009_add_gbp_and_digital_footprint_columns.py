"""Add GBP and digital footprint columns to business_units

Revision ID: 009_gbp_footprint_columns
Revises: 008_add_organization_slug
Create Date: 2026-10-12
"""
from alembic import op
from app.core.schema_sync import (
    add_column_if_missing,
    alter_column_if_exists,
    create_index_if_missing,
    create_table_if_missing,
)
import sqlalchemy as sa


revision = "009_gbp_footprint_columns"
down_revision = "008_add_organization_slug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Expand social_media_links length to 2048
    alter_column_if_exists(
        "business_units",
        "social_media_links",
        type_=sa.String(length=2048),
        existing_type=sa.String(length=1024),
        existing_nullable=True,
    )

    # 2. Add GBP attributes
    add_column_if_missing(
        "business_units",
        sa.Column("google_business_place_id", sa.String(length=255), nullable=True),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("google_business_map_url", sa.String(length=1024), nullable=True),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("google_business_rating", sa.Float(), nullable=True),
    )
    add_column_if_missing(
        "business_units",
        sa.Column("google_business_review_count", sa.Integer(), nullable=True),
    )

    # 3. Add digital footprint metrics
    add_column_if_missing(
        "business_units",
        sa.Column("digital_footprint_metrics", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("business_units", "digital_footprint_metrics")
    op.drop_column("business_units", "google_business_review_count")
    op.drop_column("business_units", "google_business_rating")
    op.drop_column("business_units", "google_business_map_url")
    op.drop_column("business_units", "google_business_place_id")
    op.alter_column(
        "business_units",
        "social_media_links",
        type_=sa.String(length=1024),
        existing_type=sa.String(length=2048),
        existing_nullable=True,
    )
