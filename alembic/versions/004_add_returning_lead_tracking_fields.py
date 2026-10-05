"""Add returning lead tracking fields

Revision ID: 004_add_returning_lead_tracking_fields
Revises: 003_add_is_frozen_to_users
Create Date: 2026-10-05

Adds three columns to the `leads` table to support the Option B
returning-lead detection strategy:

  submission_count  — total number of times this lead has submitted
  is_returning      — True after the first re-engagement
  last_activity_at  — timestamp of most recent submission (used for feed sort)
"""
from alembic import op
import sqlalchemy as sa

revision = "004_add_returning_lead_tracking_fields"
down_revision = "003_add_is_frozen_to_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # submission_count: starts at 1 for all existing leads
    op.add_column(
        "leads",
        sa.Column(
            "submission_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    # is_returning: False for all existing leads
    op.add_column(
        "leads",
        sa.Column(
            "is_returning",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # last_activity_at: back-fill with created_at for all existing rows,
    # new rows default to insert time via server_default
    op.add_column(
        "leads",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            # Back-fill existing rows with their original created_at value
            server_default=sa.text("created_at"),
        ),
    )

    # Index for fast Lead Feed ORDER BY last_activity_at DESC
    op.create_index(
        op.f("ix_leads_last_activity_at"),
        "leads",
        ["last_activity_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_leads_last_activity_at"), table_name="leads")
    op.drop_column("leads", "last_activity_at")
    op.drop_column("leads", "is_returning")
    op.drop_column("leads", "submission_count")
