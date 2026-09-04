"""Add a real, persisted `type` column to tickets.

The "Ticket Type" selector (Request/Incident) already existed in the
frontend form but was never sent to or stored by the backend — every ticket
silently fell back to "request" everywhere it was displayed. This adds the
column (defaulting existing rows to "request", which matches that fallback)
and extends the type set with "problem" and "change" for lightweight ITSM
workflows (root-cause tickets, planned changes with an approval trail via
the existing per-ticket approvals feature).

Revision ID: 052
Revises: 051
"""
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS type VARCHAR(20) "
        "NOT NULL DEFAULT 'request'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_type ON tickets (type)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tickets_type")
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS type")
