"""SLA v2 — enterprise SLA module

Adds:
  - slastatus enum (not_started | active | paused | completed | overdue)
  - sla_start_time    TIMESTAMPTZ  – when SLA timer started
  - sla_due_time      TIMESTAMPTZ  – absolute deadline (replaces sla_due_at)
  - sla_status        slastatus    – current SLA state
  - sla_paused_seconds INTEGER     – total accumulated pause seconds

Migrates existing data:
  - sla_due_time ← sla_due_at
  - sla_start_time ← created_at (for tickets that already have an SLA)
  - sla_status derived from current ticket status + due time
  - SLA now only starts when a ticket is BOTH created AND assigned;
    existing unassigned tickets get not_started even if they had sla_due_at

Revision ID: 009
Revises: 008
Create Date: 2026-04-07 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create slastatus enum safely (idempotent via DO block)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE slastatus AS ENUM ('not_started','active','paused','completed','overdue');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """)

    # 2. Add new columns idempotently
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sla_start_time     TIMESTAMPTZ")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sla_due_time       TIMESTAMPTZ")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sla_status         slastatus NOT NULL DEFAULT 'not_started'")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS sla_paused_seconds INTEGER   NOT NULL DEFAULT 0")

    # 3. Add indexes idempotently
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_sla_status   ON tickets (sla_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_sla_due_time ON tickets (sla_due_time)")

    # 4. Migrate existing data
    # Copy sla_due_at → sla_due_time for tickets that already had an SLA
    op.execute("""
        UPDATE tickets
        SET sla_due_time = sla_due_at::timestamptz
        WHERE sla_due_at IS NOT NULL
    """)

    # Set sla_start_time = created_at for tickets that had an SLA AND are assigned
    op.execute("""
        UPDATE tickets
        SET sla_start_time = created_at
        WHERE sla_due_at IS NOT NULL
          AND assignee_id IS NOT NULL
    """)

    # Set sla_status based on current ticket state:
    # - resolved/closed → completed
    # - on-hold with SLA → paused
    # - active with SLA past due → overdue
    # - active with SLA → active
    # - assigned but no SLA yet → not_started (shouldn't happen, but guard)
    # - unassigned or no SLA → not_started
    op.execute("""
        UPDATE tickets
        SET sla_status = CASE
            WHEN status IN ('resolved', 'closed') AND sla_due_time IS NOT NULL
                THEN 'completed'::slastatus
            WHEN status = 'on_hold' AND sla_due_time IS NOT NULL
                THEN 'paused'::slastatus
            WHEN sla_due_time IS NOT NULL AND sla_due_time < NOW() AND assignee_id IS NOT NULL
                THEN 'overdue'::slastatus
            WHEN sla_due_time IS NOT NULL AND assignee_id IS NOT NULL
                THEN 'active'::slastatus
            ELSE 'not_started'::slastatus
        END
    """)


def downgrade() -> None:
    op.drop_index("ix_tickets_sla_due_time",  table_name="tickets")
    op.drop_index("ix_tickets_sla_status",    table_name="tickets")
    op.drop_column("tickets", "sla_paused_seconds")
    op.drop_column("tickets", "sla_status")
    op.drop_column("tickets", "sla_due_time")
    op.drop_column("tickets", "sla_start_time")
    op.execute("DROP TYPE slastatus")
