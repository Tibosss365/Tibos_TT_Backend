"""Add is_approval flag to notifications.

Approval-request notifications are pinned/priority: they sort above other
notifications and are NOT removed by the "Clear" action (which only clears
regular notifications).

Revision ID: 039
Revises: 038
"""
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notifications "
        "ADD COLUMN IF NOT EXISTS is_approval BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE notifications DROP COLUMN IF EXISTS is_approval"
    )
