"""Store the email body on email_ticket_log so a filtered/duplicate/error
entry can be manually converted into a ticket later.

Revision ID: 056
Revises: 055
"""
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE email_ticket_log ADD COLUMN IF NOT EXISTS body TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE email_ticket_log DROP COLUMN IF EXISTS body")
