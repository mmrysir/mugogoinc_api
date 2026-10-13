"""Add GSC OAuth token and metrics columns to business_units

Revision ID: 010_add_gsc_oauth_and_metrics_columns
Revises: 009_add_gbp_and_digital_footprint_columns
Create Date: 2026-10-13

Context
-------
Adds the three columns required for the GSC OAuth handshake and telemetry cache:

  gsc_site_url      — The GSC property URL for this BU (e.g. "https://example.com"
                      or "sc-domain:example.com"). Duplicates the semantics of
                      google_search_console_site but is the authoritative field used
                      by the OAuth token refresh path.
  gsc_access_token  — Short-lived Google OAuth 2.0 access token (AES-encrypted at rest).
  gsc_refresh_token — Long-lived refresh token used to obtain new access tokens.
  gsc_metrics       — JSONB blob caching the latest Search Analytics response
                      (clicks, impressions, CTR, position, top queries, top pages).

All columns are nullable so existing rows are unaffected.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "010_add_gsc_oauth_and_metrics_columns"
down_revision = "009_add_gbp_and_digital_footprint_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. gsc_site_url (canonical property identifier for token refresh)
    op.add_column(
        "business_units",
        sa.Column("gsc_site_url", sa.String(length=512), nullable=True),
    )

    # 2. gsc_access_token (short-lived; stored encrypted)
    op.add_column(
        "business_units",
        sa.Column("gsc_access_token", sa.Text(), nullable=True),
    )

    # 3. gsc_refresh_token (long-lived; stored encrypted)
    op.add_column(
        "business_units",
        sa.Column("gsc_refresh_token", sa.Text(), nullable=True),
    )

    # 4. gsc_metrics (JSONB telemetry cache)
    op.add_column(
        "business_units",
        sa.Column(
            "gsc_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default="'{}'::jsonb",
        ),
    )


def downgrade() -> None:
    op.drop_column("business_units", "gsc_metrics")
    op.drop_column("business_units", "gsc_refresh_token")
    op.drop_column("business_units", "gsc_access_token")
    op.drop_column("business_units", "gsc_site_url")
