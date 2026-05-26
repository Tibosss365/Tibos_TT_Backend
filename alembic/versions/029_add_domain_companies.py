"""Add domain_companies table for email-domain → company mapping.

Revision ID: 029
Revises: 028
"""

from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS domain_companies (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            domain          VARCHAR(255) NOT NULL UNIQUE,
            company_name    VARCHAR(255) NOT NULL,
            contact_name    VARCHAR(150),
            contact_email   VARCHAR(255),
            contact_phone   VARCHAR(50),
            logo_url        VARCHAR(512),
            auto_discovered BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_domain_companies_domain ON domain_companies (domain);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS domain_companies;")
