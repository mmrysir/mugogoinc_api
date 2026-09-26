"""
Mako template for Alembic migration scripts. Kept minimal for scaffolded environment.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = ""  # fill-in when creating a migration
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
