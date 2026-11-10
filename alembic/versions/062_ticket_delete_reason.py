"""Persist a reason for moving tickets to Deleted Items.

Revision ID: 062
Revises: 061
"""
from alembic import op

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS delete_reason TEXT")
    # Carry forward reasons recorded as timeline comments by older clients.
    op.execute("""
        UPDATE tickets AS t
        SET delete_reason = (
            SELECT substring(tt.text FROM 25)
            FROM ticket_timeline AS tt
            WHERE tt.ticket_id = t.id
              AND tt.type::text = 'comment'
              AND tt.text LIKE 'Ticket deletion reason:%'
            ORDER BY tt.created_at DESC
            LIMIT 1
        )
        WHERE t.is_deleted = TRUE
          AND t.delete_reason IS NULL
          AND EXISTS (
            SELECT 1 FROM ticket_timeline AS tt
            WHERE tt.ticket_id = t.id
              AND tt.type::text = 'comment'
              AND tt.text LIKE 'Ticket deletion reason:%'
          )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS delete_reason")
