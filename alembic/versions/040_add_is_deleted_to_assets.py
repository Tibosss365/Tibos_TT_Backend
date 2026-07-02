"""Soft-delete flag for assets.

Assets are soft-deleted so their assignment history (asset_history, which
cascades on hard delete) survives and shows up in the global asset history.

Revision ID: 040
Revises: 039
"""
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE assets DROP COLUMN IF EXISTS is_deleted")
