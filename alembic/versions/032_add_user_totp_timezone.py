"""Add TOTP 2FA and preferred_timezone to users table.

Revision ID: 032
Revises: 031
"""
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS totp_secret          VARCHAR(64),
            ADD COLUMN IF NOT EXISTS totp_enabled         BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS totp_backup_codes    JSONB   NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS preferred_timezone   VARCHAR(50) NOT NULL DEFAULT 'UTC';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users
            DROP COLUMN IF EXISTS preferred_timezone,
            DROP COLUMN IF EXISTS totp_backup_codes,
            DROP COLUMN IF EXISTS totp_enabled,
            DROP COLUMN IF EXISTS totp_secret;
    """)
