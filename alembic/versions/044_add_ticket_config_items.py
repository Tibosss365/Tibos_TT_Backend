"""Shared ticket config items (hold reasons / resolution codes / canned responses).

Revision ID: 044
Revises: 043
"""
from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_config_items (
            id UUID PRIMARY KEY,
            kind VARCHAR(30) NOT NULL,
            label VARCHAR(255) NOT NULL,
            body TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ticket_config_items_kind "
        "ON ticket_config_items (kind)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ticket_config_items")
