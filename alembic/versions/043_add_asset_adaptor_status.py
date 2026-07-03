"""Adaptor status for assets (laptop adaptor provided / replaced).

Revision ID: 043
Revises: 042_add_asset_hardware_fields
"""
from alembic import op

revision = "043"
down_revision = "042_add_asset_hardware_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS adaptor_status VARCHAR(20) "
        "NOT NULL DEFAULT 'not_provided'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS adaptor_status")
