"""Dedup state for the condition-alert service.

Tracks which tickets have already been alerted per condition so the
60-second checker doesn't re-email the same ticket every tick.

Revision ID: 038
Revises: 037
"""
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE alert_settings ADD COLUMN IF NOT EXISTS last_condition_alerts JSONB"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE alert_settings DROP COLUMN IF EXISTS last_condition_alerts"
    )
