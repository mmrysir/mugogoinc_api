"""Initial PostgreSQL Schema for Mugogo Lead Engine & Growth OS

Revision ID: 001_initial_postgres_schema
Revises: 
Create Date: 2026-09-26 23:20:00

"""
from alembic import op
import sqlalchemy as sa

revision = '001_initial_postgres_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('phone_number', sa.String(length=20), nullable=True),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='AGENT'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # Organizations table
    op.create_table(
        'organizations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('api_key', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='ONBOARDING'),
        sa.Column('created_by_id', sa.String(length=36), nullable=True),
        sa.Column('assigned_agent_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['assigned_agent_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_organizations_api_key'), 'organizations', ['api_key'], unique=True)

    # Business Units table
    op.create_table(
        'business_units',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('route_type', sa.String(length=50), nullable=False, server_default='EXPRESS'),
        sa.Column('webhook_url', sa.String(length=500), nullable=True),
        sa.Column('owner_whatsapp_number', sa.String(length=20), nullable=False),
        sa.Column('owner_email', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_business_units_organization_id'), 'business_units', ['organization_id'], unique=False)

    # Leads table
    op.create_table(
        'leads',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('business_unit_id', sa.String(length=36), nullable=False),
        sa.Column('source_channel', sa.String(length=50), nullable=False, server_default='LANDING_PAGE'),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('phone_number', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('payload_meta', sa.JSON(), nullable=False),
        sa.Column('delivery_status', sa.String(length=50), nullable=False, server_default='PENDING'),
        sa.Column('crm_forward_status', sa.String(length=50), nullable=False, server_default='NOT_REQUIRED'),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('gclid', sa.String(length=255), nullable=True),
        sa.Column('fbclid', sa.String(length=255), nullable=True),
        sa.Column('utm_source', sa.String(length=100), nullable=True),
        sa.Column('utm_medium', sa.String(length=100), nullable=True),
        sa.Column('utm_campaign', sa.String(length=100), nullable=True),
        sa.Column('dedupe_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['business_unit_id'], ['business_units.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_leads_business_unit_id'), 'leads', ['business_unit_id'], unique=False)
    op.create_index(op.f('ix_leads_created_at'), 'leads', ['created_at'], unique=False)
    op.create_index(op.f('ix_leads_dedupe_hash'), 'leads', ['dedupe_hash'], unique=False)
    op.create_index(op.f('ix_leads_fbclid'), 'leads', ['fbclid'], unique=False)
    op.create_index(op.f('ix_leads_gclid'), 'leads', ['gclid'], unique=False)

    # Abandoned Leads table
    op.create_table(
        'abandoned_leads',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('business_unit_id', sa.String(length=36), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone_number', sa.String(length=20), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['business_unit_id'], ['business_units.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_abandoned_leads_business_unit_id'), 'abandoned_leads', ['business_unit_id'], unique=False)

    # Posts table (Subdirectory Blogging Hub)
    op.create_table(
        'posts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=True),
        sa.Column('business_unit_id', sa.String(length=36), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('excerpt', sa.Text(), nullable=True),
        sa.Column('content_markdown', sa.Text(), nullable=True),
        sa.Column('content_html', sa.Text(), nullable=True),
        sa.Column('featured_image_url', sa.String(length=500), nullable=True),
        sa.Column('author_name', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='draft'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('meta_title', sa.String(length=255), nullable=True),
        sa.Column('meta_description', sa.Text(), nullable=True),
        sa.Column('target_keywords', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['business_unit_id'], ['business_units.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_posts_business_unit_id'), 'posts', ['business_unit_id'], unique=False)
    op.create_index(op.f('ix_posts_organization_id'), 'posts', ['organization_id'], unique=False)
    op.create_index(op.f('ix_posts_published_at'), 'posts', ['published_at'], unique=False)
    op.create_index(op.f('ix_posts_slug'), 'posts', ['slug'], unique=True)
    op.create_index(op.f('ix_posts_status'), 'posts', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('posts')
    op.drop_table('abandoned_leads')
    op.drop_table('leads')
    op.drop_table('business_units')
    op.drop_table('organizations')
    op.drop_table('users')
