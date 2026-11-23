"""Add slug to organizations table

Revision ID: 008_add_organization_slug
Revises: 007_bu_profile_gsc_columns
Create Date: 2026-10-11

Context:
Adds a unique, indexed `slug` column to `organizations` to support human-readable
multi-tenant routing and atomic onboarding.
Safely backfills existing rows before applying NOT NULL constraint.
"""
from alembic import op
from app.core.schema_sync import (
    add_column_if_missing,
    alter_column_if_exists,
    create_index_if_missing,
    create_table_if_missing,
)
import sqlalchemy as sa


revision = "008_add_organization_slug"
down_revision = "007_bu_profile_gsc_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add column as nullable initially
    add_column_if_missing(
        "organizations",
        sa.Column("slug", sa.String(length=255), nullable=True),
    )

    # 2. Backfill existing rows using SQL regex replacement & unique suffix from id
    op.execute(
        """
        UPDATE organizations
        SET slug = lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g')) || '-' || substring(id, 1, 8)
        WHERE slug IS NULL;
        """
    )

    # 3. Enforce NOT NULL and create unique index
    alter_column_if_exists(
        "organizations",
        "slug",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    create_index_if_missing(
        op.f("ix_organizations_slug"),
        "organizations",
        ["slug"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_column("organizations", "slug")
