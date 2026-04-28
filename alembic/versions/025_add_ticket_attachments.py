"""add ticket_attachments table

Revision ID: 025
Revises: 024
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ticket_attachments (
            id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            ticket_id    UUID        NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            filename     VARCHAR(500) NOT NULL,
            content_type VARCHAR(200) NOT NULL DEFAULT 'application/octet-stream',
            size         INTEGER      NOT NULL DEFAULT 0,
            content      BYTEA        NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ticket_attachments_ticket_id "
        "ON ticket_attachments (ticket_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ticket_attachments")
