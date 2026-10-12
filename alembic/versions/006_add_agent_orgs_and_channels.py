"""add agent_org_assignments and client_marketing_channels

Revision ID: 006
Revises: 005_add_is_partial_to_leads
Create Date: 2026-10-06 19:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005_add_is_partial_to_leads'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Create agent_org_assignments junction table with composite uniqueness
    op.create_table(
        'agent_org_assignments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('agent_id', sa.String(36), nullable=False),
        sa.Column('org_id', sa.String(36), nullable=False),
        sa.Column('assigned_by_id', sa.String(36), nullable=True),
        sa.Column('is_primary', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('agent_id', 'org_id', name='uq_agent_org_assignment')
    )
    op.create_index('ix_agent_org_assignments_agent_id', 'agent_org_assignments', ['agent_id'])
    op.create_index('ix_agent_org_assignments_org_id', 'agent_org_assignments', ['org_id'])

    # 2. Create client_marketing_channels table with unified configuration & JSON columns
    op.create_table(
        'client_marketing_channels',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('org_id', sa.String(36), nullable=False),
        sa.Column('website_url', sa.String(255), nullable=True),
        sa.Column('gsc_domain_property', sa.String(255), nullable=True),
        sa.Column('cpl_threshold_usd', sa.Float(), nullable=True),
        sa.Column('monthly_ad_budget_usd', sa.Float(), nullable=True),
        sa.Column('alert_phone', sa.String(50), nullable=True),
        sa.Column('encrypted_oauth_tokens', sa.Text(), nullable=True),
        sa.Column('gsc_platform_properties', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('org_id')
    )
    op.create_index('ix_client_marketing_channels_org_id', 'client_marketing_channels', ['org_id'])

def downgrade() -> None:
    op.drop_index('ix_client_marketing_channels_org_id', table_name='client_marketing_channels')
    op.drop_table('client_marketing_channels')
    
    op.drop_index('ix_agent_org_assignments_org_id', table_name='agent_org_assignments')
    op.drop_index('ix_agent_org_assignments_agent_id', table_name='agent_org_assignments')
    op.drop_table('agent_org_assignments')
