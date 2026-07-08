"""Add is_trashed flag to email threads (Deleted Items / Trash folder).

Revision ID: 046
Revises: 045
"""
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE email_threads ADD COLUMN IF NOT EXISTS is_trashed "
        "BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE email_threads DROP COLUMN IF EXISTS is_trashed")
