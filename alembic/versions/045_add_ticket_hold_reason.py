"""Add hold_reason column to tickets (for reporting the on-hold reason).

Revision ID: 045
Revises: 044
"""
from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS hold_reason VARCHAR(255)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS hold_reason")
