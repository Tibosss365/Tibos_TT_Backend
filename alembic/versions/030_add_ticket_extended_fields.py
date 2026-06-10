"""Add extended ticket fields: source, tags, FRT, reopen_count, CSAT, custom fields, due_date.

Revision ID: 030
Revises: 029
"""
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tickets
            ADD COLUMN IF NOT EXISTS source            VARCHAR(20)  NOT NULL DEFAULT 'portal',
            ADD COLUMN IF NOT EXISTS tags              JSONB        NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS first_responded_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS reopen_count      INTEGER      NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS csat_rating       SMALLINT,
            ADD COLUMN IF NOT EXISTS csat_comment      TEXT,
            ADD COLUMN IF NOT EXISTS csat_sent_at      TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS csat_token        VARCHAR(64),
            ADD COLUMN IF NOT EXISTS custom_field_data JSONB        NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS due_date          TIMESTAMPTZ;
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_csat_token
            ON tickets (csat_token)
            WHERE csat_token IS NOT NULL;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_tickets_tags ON tickets USING gin (tags);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tickets_tags;")
    op.execute("DROP INDEX IF EXISTS ix_tickets_csat_token;")
    op.execute("""
        ALTER TABLE tickets
            DROP COLUMN IF EXISTS due_date,
            DROP COLUMN IF EXISTS custom_field_data,
            DROP COLUMN IF EXISTS csat_token,
            DROP COLUMN IF EXISTS csat_sent_at,
            DROP COLUMN IF EXISTS csat_comment,
            DROP COLUMN IF EXISTS csat_rating,
            DROP COLUMN IF EXISTS reopen_count,
            DROP COLUMN IF EXISTS first_responded_at,
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS source;
    """)
