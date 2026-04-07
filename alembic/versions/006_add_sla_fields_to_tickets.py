"""add sla_due_at and sla_paused_at to tickets, backfill existing tickets

Revision ID: 006
Revises: 005
Create Date: 2026-04-07 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default SLA hours per priority (used for backfill if no SLAConfig row exists)
_DEFAULT_HOURS = {"critical": 1, "high": 4, "medium": 8, "low": 24}


def upgrade() -> None:
    # 1. Add columns
    op.add_column("tickets", sa.Column("sla_due_at",    sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("sla_paused_at", sa.DateTime(timezone=True), nullable=True))

    # 2. Seed default SLAConfig row if the table is empty
    op.execute("""
        INSERT INTO sla_config (id, critical_hours, high_hours, medium_hours, low_hours, updated_at)
        SELECT gen_random_uuid(), 1, 4, 8, 24, NOW()
        WHERE NOT EXISTS (SELECT 1 FROM sla_config)
    """)

    # 3. Backfill sla_due_at for existing open/in-progress tickets
    #    Uses the SLA hours from sla_config (just seeded above if it was missing).
    #    on-hold tickets are left NULL — their SLA is paused; resolved/closed don't need it.
    op.execute("""
        UPDATE tickets t
        SET sla_due_at = t.created_at + (
            CASE t.priority
                WHEN 'critical'    THEN (SELECT critical_hours FROM sla_config LIMIT 1)
                WHEN 'high'        THEN (SELECT high_hours     FROM sla_config LIMIT 1)
                WHEN 'medium'      THEN (SELECT medium_hours   FROM sla_config LIMIT 1)
                WHEN 'low'         THEN (SELECT low_hours      FROM sla_config LIMIT 1)
                ELSE 8
            END * INTERVAL '1 hour'
        )
        WHERE t.status NOT IN ('resolved', 'closed', 'on-hold')
          AND t.sla_due_at IS NULL
    """)


def downgrade() -> None:
    op.drop_column("tickets", "sla_paused_at")
    op.drop_column("tickets", "sla_due_at")
