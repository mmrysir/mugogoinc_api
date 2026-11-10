"""Add invitations table and active_organization_id to users

Revision ID: 011_add_invitations_and_active_org
Revises: 010_add_gsc_oauth_and_metrics_columns
Create Date: 2026-10-17

Context
-------
Creates the ``invitations`` table for the Join Business flow and adds
``active_organization_id`` to ``users`` for workspace switching.

The ``invitations`` table stores pending/accepted/declined invitations
that allow users to join organizations with a specific role. Each
invitation has a secure 32-char token for email-based verification.

The ``active_organization_id`` column on ``users`` persists which
organization context the user last selected in the workspace switcher.
"""
from alembic import op
import sqlalchemy as sa

revision = "011_add_invitations_and_active_org"
down_revision = "010_add_gsc_oauth_and_metrics_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create invitations table
    op.create_table(
        "invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("invited_email", sa.String(255), nullable=False, index=True),
        sa.Column(
            "role",
            sa.Enum("MANAGER", "AGENT", name="invitation_role"),
            nullable=False,
            server_default="AGENT",
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "ACCEPTED", "DECLINED", "EXPIRED", name="invitation_status"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("access_token", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "invited_by_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. Add active_organization_id to users for workspace context persistence
    op.add_column(
        "users",
        sa.Column(
            "active_organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "active_organization_id")
    op.drop_table("invitations")
    # Clean up enum types
    sa.Enum(name="invitation_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="invitation_status").drop(op.get_bind(), checkfirst=True)
