"""Add From/To/CC/Subject columns to ticket_timeline so email_in/email_out
entries can render as a proper threaded email view (sender, recipients,
subject) instead of a generic text-only note.

Revision ID: 057
Revises: 056
"""
from alembic import op

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ticket_timeline ADD COLUMN IF NOT EXISTS email_from TEXT")
    op.execute("ALTER TABLE ticket_timeline ADD COLUMN IF NOT EXISTS email_to TEXT")
    op.execute("ALTER TABLE ticket_timeline ADD COLUMN IF NOT EXISTS email_cc TEXT")
    op.execute("ALTER TABLE ticket_timeline ADD COLUMN IF NOT EXISTS email_subject TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE ticket_timeline DROP COLUMN IF EXISTS email_from")
    op.execute("ALTER TABLE ticket_timeline DROP COLUMN IF EXISTS email_to")
    op.execute("ALTER TABLE ticket_timeline DROP COLUMN IF EXISTS email_cc")
    op.execute("ALTER TABLE ticket_timeline DROP COLUMN IF EXISTS email_subject")
