"""Add GBP and digital footprint columns to business_units

Revision ID: 009_add_gbp_and_digital_footprint_columns
Revises: 008_add_organization_slug
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa


revision = "009_add_gbp_and_digital_footprint_columns"
down_revision = "008_add_organization_slug"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Expand social_media_links length to 2048
    op.alter_column(
        "business_units",
        "social_media_links",
        type_=sa.String(length=2048),
        existing_type=sa.String(length=1024),
        existing_nullable=True,
    )

    # 2. Add GBP attributes
    op.add_column(
        "business_units",
        sa.Column("google_business_place_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "business_units",
        sa.Column("google_business_map_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "business_units",
        sa.Column("google_business_rating", sa.Float(), nullable=True),
    )
    op.add_column(
        "business_units",
        sa.Column("google_business_review_count", sa.Integer(), nullable=True),
    )

    # 3. Add digital footprint metrics
    op.add_column(
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
