"""add email_thread_id to tickets and email_out/email_in to timelinetype enum

Revision ID: 008
Revises: 007
Create Date: 2026-04-07 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add email_thread_id to tickets
    op.add_column("tickets", sa.Column("email_thread_id", sa.String(500), nullable=True))
    op.create_index("ix_tickets_email_thread_id", "tickets", ["email_thread_id"])

    # 2. Extend timelinetype enum with email_out and email_in
    op.execute("ALTER TYPE timelinetype ADD VALUE IF NOT EXISTS 'email_out'")
    op.execute("ALTER TYPE timelinetype ADD VALUE IF NOT EXISTS 'email_in'")


def downgrade() -> None:
    op.drop_index("ix_tickets_email_thread_id", table_name="tickets")
    op.drop_column("tickets", "email_thread_id")
    # Note: PostgreSQL does not support removing enum values without recreation
