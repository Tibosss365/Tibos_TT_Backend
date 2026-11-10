"""Smart (keyword) filter settings on the inbound email config.

Revision ID: 063
Revises: 062
"""
from alembic import op

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE inbound_email_config ADD COLUMN IF NOT EXISTS smart_filter JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE inbound_email_config DROP COLUMN IF EXISTS smart_filter")
