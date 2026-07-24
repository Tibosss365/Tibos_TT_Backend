"""Add account owners to domain_companies and tickets.

Owners are the internal staff who own a customer account. Stored as a JSONB
list of {user_id, name, email} so a company (and a ticket) can carry several.

Revision ID: 050
Revises: 049
"""
from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE domain_companies ADD COLUMN IF NOT EXISTS owners JSONB "
        "NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS owners JSONB "
        "NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tickets DROP COLUMN IF EXISTS owners")
    op.execute("ALTER TABLE domain_companies DROP COLUMN IF EXISTS owners")
