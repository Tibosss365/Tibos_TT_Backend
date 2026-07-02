"""Add processor, ram, rom to assets table

Revision ID: 042_add_asset_hardware_fields
Revises: 041_add_asset_extended_fields
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = '042_add_asset_hardware_fields'
down_revision = '041'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('assets', sa.Column('processor', sa.String(150), nullable=True))
    op.add_column('assets', sa.Column('ram', sa.String(50), nullable=True))
    op.add_column('assets', sa.Column('rom', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('assets', 'rom')
    op.drop_column('assets', 'ram')
    op.drop_column('assets', 'processor')
