"""Add brand, specification, os_version, asset_number to assets.

Revision ID: 041
Revises: 040
"""
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE assets
            ADD COLUMN IF NOT EXISTS brand         VARCHAR(100),
            ADD COLUMN IF NOT EXISTS specification TEXT,
            ADD COLUMN IF NOT EXISTS os_version    VARCHAR(100),
            ADD COLUMN IF NOT EXISTS asset_number  VARCHAR(50)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE assets
            DROP COLUMN IF EXISTS brand,
            DROP COLUMN IF EXISTS specification,
            DROP COLUMN IF EXISTS os_version,
            DROP COLUMN IF EXISTS asset_number
    """)
