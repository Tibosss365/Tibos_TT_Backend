"""Add login_sessions table for persistent login history.

Revision ID: 033
Revises: 032
"""
from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS login_sessions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
            ip_address      VARCHAR(45),
            user_agent      TEXT,
            browser         VARCHAR(60),
            os              VARCHAR(60),
            logged_in_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            logged_out_at   TIMESTAMPTZ,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_login_sessions_user_id
            ON login_sessions(user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_login_sessions_logged_in_at
            ON login_sessions(logged_in_at DESC)
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS login_sessions;
    """)
