"""Add must_change_password flag to users for admin force-reset feature.

Revision ID: 034
Revises: 033
"""
from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users DROP COLUMN IF EXISTS must_change_password
    """)
