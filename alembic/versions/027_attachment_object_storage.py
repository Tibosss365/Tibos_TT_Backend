"""Add object storage columns to ticket_attachments; make content nullable.

Revision ID: 027
Revises: 026
"""

from alembic import op
import sqlalchemy as sa

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS / conditional checks so this migration is
    # safe to run even when the columns were added outside of alembic tracking.
    op.execute("""
        DO $$
        BEGIN
            -- Make content nullable (safe to run repeatedly)
            ALTER TABLE ticket_attachments ALTER COLUMN content DROP NOT NULL;
        EXCEPTION
            WHEN others THEN NULL;
        END
        $$;
    """)
    op.execute("""
        ALTER TABLE ticket_attachments
            ADD COLUMN IF NOT EXISTS storage_key  VARCHAR(2000),
            ADD COLUMN IF NOT EXISTS storage_url  VARCHAR(2000),
            ADD COLUMN IF NOT EXISTS is_inline    BOOLEAN NOT NULL DEFAULT false;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ticket_attachments
            DROP COLUMN IF EXISTS is_inline,
            DROP COLUMN IF EXISTS storage_url,
            DROP COLUMN IF EXISTS storage_key;
    """)
    op.execute("""
        UPDATE ticket_attachments SET content = '' WHERE content IS NULL;
    """)
    op.alter_column("ticket_attachments", "content", nullable=False)
