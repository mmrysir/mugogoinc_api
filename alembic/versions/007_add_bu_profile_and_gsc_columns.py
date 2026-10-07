"""Add BU profile columns and google_search_console_site

Revision ID: 007_add_bu_profile_and_gsc_columns
Revises: 006
Create Date: 2026-10-07

Context
-------
Migration 001 created the `business_units` table with only the core
operational fields (id, organization_id, name, route_type, webhook_url,
owner_whatsapp_number, owner_email, created_at).

The following profile / marketing integration columns were added to the
SQLAlchemy model *without* a corresponding migration, meaning live databases
created from migration files are missing these columns:

  primary_url              — The brand's primary website URL
  social_media_links       — JSON-serialised string of social profile handles
  google_ads_customer_id   — Per-BU Google Ads customer ID override
  target_country           — Google Ads geo-target constant (default: 2404 = Kenya)
  target_language          — Google Ads language constant (default: 1000 = English)

This migration also formally adds the new column decided in the D1
architectural review (2026-10-07):

  google_search_console_site — GSC property string, e.g. "https://example.com"
                               or "sc-domain:example.com". Stored at the BU
                               level to support multi-brand orgs with separate
                               verified GSC properties per brand/website.

All added columns are nullable with no server_default so existing rows
receive NULL — which is correct since these are optional profile fields.

Downgrade restores the table to the migration-001 baseline.
"""
from alembic import op
import sqlalchemy as sa

revision = "007_add_bu_profile_and_gsc_columns"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. primary_url ────────────────────────────────────────────────────────
    op.add_column(
        "business_units",
        sa.Column("primary_url", sa.String(length=512), nullable=True),
    )

    # ── 2. social_media_links ─────────────────────────────────────────────────
    op.add_column(
        "business_units",
        sa.Column("social_media_links", sa.String(length=1024), nullable=True),
    )

    # ── 3. google_ads_customer_id ─────────────────────────────────────────────
    op.add_column(
        "business_units",
        sa.Column("google_ads_customer_id", sa.String(length=50), nullable=True),
    )

    # ── 4. google_search_console_site (NEW — D1 decision) ────────────────────
    op.add_column(
        "business_units",
        sa.Column("google_search_console_site", sa.String(length=512), nullable=True),
    )

    # ── 5. target_country ─────────────────────────────────────────────────────
    # Default "2404" (Kenya) for new rows; NULL for existing rows is acceptable
    # since the Keyword Planner service already falls back to "2404" in code.
    op.add_column(
        "business_units",
        sa.Column(
            "target_country",
            sa.String(length=10),
            nullable=True,
            server_default="2404",
        ),
    )

    # ── 6. target_language ────────────────────────────────────────────────────
    # Default "1000" (English) for new rows; same fallback rationale as above.
    op.add_column(
        "business_units",
        sa.Column(
            "target_language",
            sa.String(length=10),
            nullable=True,
            server_default="1000",
        ),
    )


def downgrade() -> None:
    op.drop_column("business_units", "target_language")
    op.drop_column("business_units", "target_country")
    op.drop_column("business_units", "google_search_console_site")
    op.drop_column("business_units", "google_ads_customer_id")
    op.drop_column("business_units", "social_media_links")
    op.drop_column("business_units", "primary_url")
