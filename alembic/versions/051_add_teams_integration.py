"""Add Microsoft Teams two-way chat integration tables.

teams_config: singleton row (Azure Bot App ID / secret / tenant).
teams_conversations: links a Teams conversation to a ticket so replies in
either direction stay in sync.

Revision ID: 051
Revises: 050
"""
from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS teams_config (
            id INTEGER PRIMARY KEY DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT false,
            tenant_id VARCHAR(255),
            app_id VARCHAR(255),
            app_password TEXT,
            default_category VARCHAR(80) NOT NULL DEFAULT 'other',
            default_priority VARCHAR(20) NOT NULL DEFAULT 'medium',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS teams_conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            conversation_id VARCHAR(500) NOT NULL UNIQUE,
            service_url VARCHAR(500) NOT NULL,
            tenant_id VARCHAR(255),
            aad_user_id VARCHAR(255),
            aad_user_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_teams_conversations_ticket_id "
        "ON teams_conversations (ticket_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_teams_conversations_conversation_id "
        "ON teams_conversations (conversation_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS teams_conversations")
    op.execute("DROP TABLE IF EXISTS teams_config")
