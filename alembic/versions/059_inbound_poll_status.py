"""Store inbound email polling attempts and failures.

Revision ID: 059
Revises: 058
"""
from alembic import op
import sqlalchemy as sa


revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbound_email_config",
        sa.Column("last_poll_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inbound_email_config",
        sa.Column("last_poll_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inbound_email_config", "last_poll_error")
    op.drop_column("inbound_email_config", "last_poll_attempt_at")
