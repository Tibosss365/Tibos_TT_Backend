"""Add 'user' role and requester_id to tickets

Revision ID: 023
Revises: 022
Create Date: 2026-04-27 00:00:00
"""
from typing import Sequence, Union
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL enum ADD VALUE must not run inside a transaction block.
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'user'")

    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS requester_id UUID "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tickets_requester_id ON tickets (requester_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tickets_requester_id")
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS requester_id")
    # PostgreSQL does not support removing enum values.
