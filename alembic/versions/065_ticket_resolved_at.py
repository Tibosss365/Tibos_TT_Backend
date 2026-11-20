"""tickets.resolved_at — when a ticket was resolved/closed (for "time to resolve").

Existing resolved/closed tickets are filled from their history: the "resolved"
entry first, then the status change that said "closed", and only if neither
exists the last-updated time (an approximation).

Revision ID: 065
Revises: 064
"""
from alembic import op

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ")
    op.execute("""
        UPDATE tickets t SET resolved_at = h.ts
        FROM (SELECT ticket_id, MAX(created_at) AS ts FROM ticket_timeline
              WHERE type::text = 'resolved' GROUP BY ticket_id) h
        WHERE h.ticket_id = t.id AND t.status::text IN ('resolved', 'closed') AND t.resolved_at IS NULL
    """)
    op.execute("""
        UPDATE tickets t SET resolved_at = h.ts
        FROM (SELECT ticket_id, MAX(created_at) AS ts FROM ticket_timeline
              WHERE type::text = 'status' AND text ILIKE '%to <strong>closed</strong>%' GROUP BY ticket_id) h
        WHERE h.ticket_id = t.id AND t.status::text IN ('resolved', 'closed') AND t.resolved_at IS NULL
    """)
    op.execute("""
        UPDATE tickets SET resolved_at = updated_at
        WHERE status::text IN ('resolved', 'closed') AND resolved_at IS NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS resolved_at")
