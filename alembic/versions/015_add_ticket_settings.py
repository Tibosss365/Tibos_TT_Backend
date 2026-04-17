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
    # 1. Create ticket_settings table (single-row config)
    op.create_table(
        "ticket_settings",
        sa.Column("id",               PG_UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("number_prefix",    sa.String(20),  nullable=False, server_default="TKT"),
        sa.Column("number_digits",    sa.Integer(),   nullable=False, server_default="4"),
        sa.Column("default_status",   sa.String(20),  nullable=False, server_default="open"),
        sa.Column("default_priority", sa.String(20),  nullable=False, server_default="medium"),
        sa.Column("updated_at",       sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # Seed one default row so GET /admin/ticket-settings always returns data
    op.execute(
        f"INSERT INTO ticket_settings (id, number_prefix, number_digits, default_status, default_priority, updated_at) "
        f"VALUES ('{uuid.uuid4()}', 'TKT', 4, 'open', 'medium', now()) "
        f"ON CONFLICT DO NOTHING"
    )

    # 2. Add prefix / digit columns to the tickets table
    #    nullable so existing rows are unaffected; app falls back to 'TKT'/4 for NULL rows.
    op.add_column("tickets",
        sa.Column("ticket_prefix",        sa.String(20), nullable=True, server_default="TKT"))
    op.add_column("tickets",
        sa.Column("ticket_number_digits", sa.Integer(),  nullable=True, server_default="4"))

    # Backfill existing tickets so their ticket_id property stays TKT-XXXX
    op.execute("UPDATE tickets SET ticket_prefix = 'TKT', ticket_number_digits = 4 WHERE ticket_prefix IS NULL")


def downgrade() -> None:
    op.drop_column("tickets", "ticket_number_digits")
    op.drop_column("tickets", "ticket_prefix")
    op.drop_table("ticket_settings")
