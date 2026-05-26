"""Add is_deleted and deleted_at columns to tickets for server-side soft delete.

Revision ID: 028
Revises: 027
"""

from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tickets
            ADD COLUMN IF NOT EXISTS is_deleted  BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS deleted_at  TIMESTAMPTZ;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tickets_is_deleted ON tickets (is_deleted);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tickets_is_deleted;")
    op.execute("""
        ALTER TABLE tickets
            DROP COLUMN IF EXISTS deleted_at,
            DROP COLUMN IF EXISTS is_deleted;
    """)
