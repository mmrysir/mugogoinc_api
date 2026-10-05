"""Add is_partial column to leads table

Revision ID: 005_add_is_partial_to_leads
Revises: 004_add_returning_lead_tracking_fields
Create Date: 2026-10-05

Adds is_partial column (boolean, default False) to the `leads` table to support
Partial Lead protection and UI badging.
"""
from alembic import op
import sqlalchemy as sa

revision = "005_add_is_partial_to_leads"
down_revision = "004_add_returning_lead_tracking_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "is_partial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "is_partial")
