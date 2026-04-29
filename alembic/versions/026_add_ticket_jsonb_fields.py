"""add tasks, work_log, reminders, approvals JSONB columns to tickets

Revision ID: 026
Revises: 025
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("tasks",     JSONB, nullable=False, server_default="'[]'::jsonb"))
    op.add_column("tickets", sa.Column("work_log",  JSONB, nullable=False, server_default="'[]'::jsonb"))
    op.add_column("tickets", sa.Column("reminders", JSONB, nullable=False, server_default="'[]'::jsonb"))
    op.add_column("tickets", sa.Column("approvals", JSONB, nullable=False, server_default="'[]'::jsonb"))


def downgrade() -> None:
    op.drop_column("tickets", "approvals")
    op.drop_column("tickets", "reminders")
    op.drop_column("tickets", "work_log")
    op.drop_column("tickets", "tasks")
