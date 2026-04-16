"""Add group_id column to tickets table

The frontend normalizeTicket() maps t.group_id → group and the Dashboard
filters/renders tickets by group.  Without this column, ticket creation
crashes (Ticket(group_id=...) → SQLAlchemy TypeError) and TicketOut
serialisation fails (group_id: str required but field missing).

Revision ID: 013
Revises: 012
Create Date: 2026-04-16 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add group_id column to tickets (idempotent — safe to re-run)
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS group_id VARCHAR(80)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tickets_group_id ON tickets (group_id)"
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_group_id", table_name="tickets")
    op.drop_column("tickets", "group_id")
