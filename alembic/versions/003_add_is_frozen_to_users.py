"""Add is_frozen column to users table

Revision ID: 003_add_is_frozen_to_users
Revises: 002_add_is_approved_to_users
Create Date: 2026-10-01

"""
from alembic import op
import sqlalchemy as sa

revision = '003_add_is_frozen_to_users'
down_revision = '002_add_is_approved_to_users'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_frozen', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('users', 'is_frozen')
