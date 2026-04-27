"""Add ticket_settings table and per-ticket prefix/digits columns

Stores admin-configurable ticket number format (prefix, digit count) and
creation defaults (status, priority). Each ticket row caches the prefix and
digit count at creation so historical IDs stay stable when settings change.

Revision ID: 015
Revises: 014
Create Date: 2026-04-17 00:00:00
"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create ticket_settings table (idempotent)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS ticket_settings (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            number_prefix    VARCHAR(20)  NOT NULL DEFAULT 'TKT',
            number_digits    INTEGER      NOT NULL DEFAULT 4,
            default_status   VARCHAR(20)  NOT NULL DEFAULT 'open',
            default_priority VARCHAR(20)  NOT NULL DEFAULT 'medium',
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        f"INSERT INTO ticket_settings (id, number_prefix, number_digits, default_status, default_priority, updated_at) "
        f"VALUES ('{uuid.uuid4()}', 'TKT', 4, 'open', 'medium', now()) "
        f"ON CONFLICT DO NOTHING"
    )

    # 2. Add prefix / digit columns to the tickets table (idempotent)
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ticket_prefix        VARCHAR(20) DEFAULT 'TKT'")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ticket_number_digits INTEGER     DEFAULT 4")
    op.execute("UPDATE tickets SET ticket_prefix = 'TKT', ticket_number_digits = 4 WHERE ticket_prefix IS NULL")


def downgrade() -> None:
    op.drop_column("tickets", "ticket_number_digits")
    op.drop_column("tickets", "ticket_prefix")
    op.drop_table("ticket_settings")
