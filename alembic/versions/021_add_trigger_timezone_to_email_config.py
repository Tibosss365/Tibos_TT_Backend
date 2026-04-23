"""Add trigger_timezone to email_config

Revision ID: 021
Revises: 020
Create Date: 2026-04-23 18:57:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '021'
down_revision = '020'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Use server_default to handle existing rows
    op.add_column('email_config', sa.Column('trigger_timezone', sa.String(length=50), server_default='UTC', nullable=False))

def downgrade() -> None:
    op.drop_column('email_config', 'trigger_timezone')
