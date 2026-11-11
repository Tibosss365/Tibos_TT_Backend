"""Auto-assign settings on ticket_settings.

Revision ID: 064
Revises: 063
"""
from alembic import op

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ticket_settings ADD COLUMN IF NOT EXISTS auto_assign_enabled BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE ticket_settings ADD COLUMN IF NOT EXISTS auto_assign_after_minutes INTEGER NOT NULL DEFAULT 30")


def downgrade() -> None:
    op.execute("ALTER TABLE ticket_settings DROP COLUMN IF EXISTS auto_assign_after_minutes")
    op.execute("ALTER TABLE ticket_settings DROP COLUMN IF EXISTS auto_assign_enabled")
