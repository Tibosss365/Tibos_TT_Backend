"""Add company_settings (org-wide profile + system settings).

This was previously stored only in the browser's localStorage (Zustand
persist on the frontend) with no backend at all — every admin/browser had
its own private, unsynced copy, which looked like it kept "resetting" since
it was never actually shared or durable. This gives it one real row.

Revision ID: 055
Revises: 054
"""
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS company_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            name VARCHAR(200) NOT NULL DEFAULT 'HelpdeskPro',
            website VARCHAR(500),
            phone VARCHAR(50),
            address TEXT,
            logo TEXT,
            language VARCHAR(10) NOT NULL DEFAULT 'en',
            timezone VARCHAR(50) NOT NULL DEFAULT 'Asia/Kolkata',
            session_timeout_minutes INTEGER NOT NULL DEFAULT 480,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "INSERT INTO company_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS company_settings")
