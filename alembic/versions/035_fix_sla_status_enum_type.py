"""Convert tickets.sla_status from VARCHAR to the slastatus enum.

On databases where the tickets table predates migration 009, sla_status was
created as VARCHAR, so queries comparing it against enum-typed parameters fail
with "operator does not exist: character varying = slastatus"
(seen on /dashboard/stats). Idempotent: no-ops when the column is already enum.

Revision ID: 035
Revises: 034
"""
from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'tickets'
                  AND column_name = 'sla_status'
                  AND data_type = 'character varying'
            ) THEN
                ALTER TABLE tickets ALTER COLUMN sla_status DROP DEFAULT;
                ALTER TABLE tickets
                    ALTER COLUMN sla_status TYPE slastatus
                    USING sla_status::slastatus;
                ALTER TABLE tickets
                    ALTER COLUMN sla_status SET DEFAULT 'not_started'::slastatus;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE tickets ALTER COLUMN sla_status DROP DEFAULT;
        ALTER TABLE tickets ALTER COLUMN sla_status TYPE VARCHAR USING sla_status::text;
        ALTER TABLE tickets ALTER COLUMN sla_status SET DEFAULT 'not_started';
    """)
