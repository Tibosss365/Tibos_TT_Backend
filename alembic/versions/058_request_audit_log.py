"""Create API request audit log.

Revision ID: 058
Revises: 057
"""
from alembic import op


revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            user_name VARCHAR(100) NULL,
            user_email VARCHAR(255) NULL,
            method VARCHAR(10) NOT NULL,
            path VARCHAR(500) NOT NULL,
            status_code INTEGER NOT NULL,
            ip_address VARCHAR(45) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_log_user_id_created_at ON audit_log(user_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log")
