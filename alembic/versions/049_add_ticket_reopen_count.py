"""Add reopen_count column to tickets.

Revision ID: 049
Revises: 048
"""
from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS reopen_count INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS reopen_count")
