"""Add agent-facing email inbox tables.

Creates email_accounts, email_threads, email_messages,
inbox_email_templates, email_signatures, email_routing_rules —
the backend for the /email page (frontend src/pages/Email).

Revision ID: 036
Revises: 035
"""
from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS email_accounts (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            email_address VARCHAR(255) NOT NULL,
            display_name VARCHAR(120),
            protocol VARCHAR(20) NOT NULL DEFAULT 'imap_smtp',
            imap_host VARCHAR(255),
            imap_port INTEGER NOT NULL DEFAULT 993,
            imap_use_ssl BOOLEAN NOT NULL DEFAULT TRUE,
            imap_username VARCHAR(255),
            imap_password TEXT,
            smtp_host VARCHAR(255),
            smtp_port INTEGER NOT NULL DEFAULT 587,
            smtp_use_tls BOOLEAN NOT NULL DEFAULT TRUE,
            smtp_username VARCHAR(255),
            smtp_password TEXT,
            graph_tenant_id VARCHAR(255),
            graph_client_id VARCHAR(255),
            graph_client_secret TEXT,
            graph_user_id VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            auto_create_tickets BOOLEAN NOT NULL DEFAULT FALSE,
            default_ticket_priority VARCHAR(20) NOT NULL DEFAULT 'medium',
            default_assign_team_id VARCHAR(80),
            last_fetched_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS email_threads (
            id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES email_accounts(id) ON DELETE CASCADE,
            ticket_id UUID REFERENCES tickets(id) ON DELETE SET NULL,
            subject VARCHAR(500) NOT NULL DEFAULT '',
            snippet VARCHAR(300),
            participant_emails JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            is_starred BOOLEAN NOT NULL DEFAULT FALSE,
            is_archived BOOLEAN NOT NULL DEFAULT FALSE,
            is_spam BOOLEAN NOT NULL DEFAULT FALSE,
            message_count INTEGER NOT NULL DEFAULT 0,
            unread_count INTEGER NOT NULL DEFAULT 0,
            has_attachments BOOLEAN NOT NULL DEFAULT FALSE,
            last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_threads_account_id ON email_threads (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_threads_ticket_id ON email_threads (ticket_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_threads_last_message_at ON email_threads (last_message_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS email_messages (
            id UUID PRIMARY KEY,
            thread_id UUID NOT NULL REFERENCES email_threads(id) ON DELETE CASCADE,
            account_id UUID NOT NULL REFERENCES email_accounts(id) ON DELETE CASCADE,
            rfc_message_id VARCHAR(500),
            in_reply_to VARCHAR(500),
            direction VARCHAR(10) NOT NULL DEFAULT 'inbound',
            message_type VARCHAR(20) NOT NULL DEFAULT 'original',
            from_email VARCHAR(255) NOT NULL DEFAULT '',
            from_name VARCHAR(255),
            sent_by_agent_id UUID REFERENCES users(id) ON DELETE SET NULL,
            to_recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
            cc_recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
            bcc_recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
            subject VARCHAR(500),
            body_html TEXT,
            body_text TEXT,
            body_stripped TEXT,
            delivery_status VARCHAR(20) NOT NULL DEFAULT 'delivered',
            delivery_error TEXT,
            sent_at TIMESTAMPTZ,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            read_at TIMESTAMPTZ,
            is_opened BOOLEAN NOT NULL DEFAULT FALSE,
            open_count INTEGER NOT NULL DEFAULT 0,
            first_opened_at TIMESTAMPTZ,
            ai_summary TEXT,
            ai_suggested_reply TEXT,
            ai_sentiment VARCHAR(10),
            attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_messages_thread_id ON email_messages (thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_messages_account_id ON email_messages (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_messages_rfc_message_id ON email_messages (rfc_message_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_messages_received_at ON email_messages (received_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS inbox_email_templates (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            category VARCHAR(80) NOT NULL DEFAULT 'general',
            subject VARCHAR(500) NOT NULL DEFAULT '',
            body_html TEXT NOT NULL DEFAULT '',
            body_text TEXT,
            variables JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_shared BOOLEAN NOT NULL DEFAULT TRUE,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS email_signatures (
            id UUID PRIMARY KEY,
            agent_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            body_html TEXT NOT NULL DEFAULT '',
            body_text TEXT,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_signatures_agent_id ON email_signatures (agent_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS email_routing_rules (
            id UUID PRIMARY KEY,
            account_id UUID REFERENCES email_accounts(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            conditions JSONB NOT NULL DEFAULT '[]'::jsonb,
            condition_logic VARCHAR(3) NOT NULL DEFAULT 'AND',
            actions JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_routing_rules_account_id ON email_routing_rules (account_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS email_routing_rules")
    op.execute("DROP TABLE IF EXISTS email_signatures")
    op.execute("DROP TABLE IF EXISTS inbox_email_templates")
    op.execute("DROP TABLE IF EXISTS email_messages")
    op.execute("DROP TABLE IF EXISTS email_threads")
    op.execute("DROP TABLE IF EXISTS email_accounts")
