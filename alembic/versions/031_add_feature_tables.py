"""Add feature tables: custom_fields, ticket_templates, automation_rules, webhook_configs,
notification_channels, assets, escalation_rules, recurring_ticket_templates, portal_branding.
Also enables pg_trgm for duplicate detection.

Revision ID: 031
Revises: 030
"""
from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: pg_trgm extension (trigram similarity) is NOT created here because
    # Azure Database for PostgreSQL requires it to be allow-listed via the portal.
    # Duplicate detection falls back to Python-side ILIKE filtering instead.

    op.execute("""
        CREATE TABLE IF NOT EXISTS custom_fields (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            name            VARCHAR(100) NOT NULL,
            field_type      VARCHAR(20)  NOT NULL DEFAULT 'text',
            options         JSONB        NOT NULL DEFAULT '[]'::jsonb,
            is_required     BOOLEAN      NOT NULL DEFAULT false,
            display_order   INTEGER      NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ticket_templates (
            id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name                 VARCHAR(100) NOT NULL,
            description          TEXT,
            subject              VARCHAR(255) NOT NULL DEFAULT '',
            category             VARCHAR(80)  NOT NULL DEFAULT 'other',
            priority             VARCHAR(20)  NOT NULL DEFAULT 'medium',
            group_id             VARCHAR(80),
            description_template TEXT,
            custom_field_data    JSONB        NOT NULL DEFAULT '{}'::jsonb,
            tags                 JSONB        NOT NULL DEFAULT '[]'::jsonb,
            created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS automation_rules (
            id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(100) NOT NULL,
            is_active   BOOLEAN      NOT NULL DEFAULT true,
            trigger     VARCHAR(50)  NOT NULL DEFAULT 'ticket_created',
            conditions  JSONB        NOT NULL DEFAULT '[]'::jsonb,
            actions     JSONB        NOT NULL DEFAULT '[]'::jsonb,
            run_order   INTEGER      NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS webhook_configs (
            id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name              VARCHAR(100) NOT NULL,
            url               VARCHAR(512) NOT NULL,
            secret            VARCHAR(128),
            events            JSONB        NOT NULL DEFAULT '[]'::jsonb,
            is_active         BOOLEAN      NOT NULL DEFAULT true,
            last_triggered_at TIMESTAMPTZ,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_channels (
            id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            channel_type VARCHAR(20)  NOT NULL DEFAULT 'slack',
            name         VARCHAR(100) NOT NULL,
            webhook_url  VARCHAR(512) NOT NULL,
            events       JSONB        NOT NULL DEFAULT '[]'::jsonb,
            is_active    BOOLEAN      NOT NULL DEFAULT true,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_tag        VARCHAR(50)  UNIQUE,
            name             VARCHAR(150) NOT NULL,
            type             VARCHAR(30)  NOT NULL DEFAULT 'other',
            serial_number    VARCHAR(100),
            manufacturer     VARCHAR(100),
            model            VARCHAR(100),
            status           VARCHAR(20)  NOT NULL DEFAULT 'active',
            assigned_to      UUID         REFERENCES users(id) ON DELETE SET NULL,
            location         VARCHAR(150),
            purchase_date    DATE,
            warranty_expiry  DATE,
            notes            TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS escalation_rules (
            id                       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name                     VARCHAR(100) NOT NULL,
            is_active                BOOLEAN      NOT NULL DEFAULT true,
            priority                 VARCHAR(20)  NOT NULL DEFAULT 'high',
            hours_before_escalation  INTEGER      NOT NULL DEFAULT 4,
            escalate_to_ids          JSONB        NOT NULL DEFAULT '[]'::jsonb,
            notify_email             VARCHAR(255),
            created_at               TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS recurring_ticket_templates (
            id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name         VARCHAR(100) NOT NULL,
            cron_expr    VARCHAR(100) NOT NULL DEFAULT '0 9 * * 1',
            subject      VARCHAR(255) NOT NULL,
            category     VARCHAR(80)  NOT NULL DEFAULT 'other',
            priority     VARCHAR(20)  NOT NULL DEFAULT 'medium',
            description  TEXT,
            assignee_id  UUID         REFERENCES users(id) ON DELETE SET NULL,
            group_id     VARCHAR(80),
            is_active    BOOLEAN      NOT NULL DEFAULT true,
            last_run_at  TIMESTAMPTZ,
            next_run_at  TIMESTAMPTZ,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS portal_branding (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            logo_url         VARCHAR(512),
            favicon_url      VARCHAR(512),
            primary_color    VARCHAR(7)   NOT NULL DEFAULT '#6366f1',
            company_name     VARCHAR(150) NOT NULL DEFAULT 'Help Desk',
            support_email    VARCHAR(255),
            welcome_message  TEXT,
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    for tbl in [
        "portal_branding", "recurring_ticket_templates", "escalation_rules",
        "assets", "notification_channels", "webhook_configs",
        "automation_rules", "ticket_templates", "custom_fields",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {tbl};")
