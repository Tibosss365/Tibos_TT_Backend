"""Multi-tier escalation ladders.

Escalation rules used to be single-tier: one hours_before_escalation
threshold, one flat escalate_to_ids/notify_email. Replaced with an ordered
`levels` JSONB ladder so a still-open ticket keeps escalating further
(team lead -> manager -> director) instead of firing once and stopping.
Existing rules are migrated into a one-level ladder so nothing is lost.

Also adds Ticket.escalation_level / last_escalated_at so the checker knows
which tier a ticket already reached (each tier fires exactly once).

Revision ID: 054
Revises: 053
"""
from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE escalation_rules ADD COLUMN IF NOT EXISTS levels JSONB "
        "NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        """
        UPDATE escalation_rules
        SET levels = jsonb_build_array(
            jsonb_build_object(
                'hours', hours_before_escalation,
                'notify_email', notify_email,
                'escalate_to_ids', COALESCE(escalate_to_ids, '[]'::jsonb)
            )
        )
        WHERE levels = '[]'::jsonb
        """
    )
    op.execute("ALTER TABLE escalation_rules DROP COLUMN IF EXISTS hours_before_escalation")
    op.execute("ALTER TABLE escalation_rules DROP COLUMN IF EXISTS escalate_to_ids")
    op.execute("ALTER TABLE escalation_rules DROP COLUMN IF EXISTS notify_email")

    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS escalation_level INTEGER "
        "NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS last_escalated_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS last_escalated_at")
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS escalation_level")

    op.execute(
        "ALTER TABLE escalation_rules ADD COLUMN IF NOT EXISTS hours_before_escalation "
        "INTEGER NOT NULL DEFAULT 4"
    )
    op.execute(
        "ALTER TABLE escalation_rules ADD COLUMN IF NOT EXISTS escalate_to_ids JSONB "
        "NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE escalation_rules ADD COLUMN IF NOT EXISTS notify_email VARCHAR(255)"
    )
    op.execute(
        """
        UPDATE escalation_rules
        SET hours_before_escalation = COALESCE((levels->0->>'hours')::int, 4),
            notify_email = levels->0->>'notify_email',
            escalate_to_ids = COALESCE(levels->0->'escalate_to_ids', '[]'::jsonb)
        WHERE jsonb_array_length(levels) > 0
        """
    )
    op.execute("ALTER TABLE escalation_rules DROP COLUMN IF EXISTS levels")
