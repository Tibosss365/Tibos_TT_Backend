"""dynamic categories + change ticket.category to varchar

Revision ID: 004
Revises: 003
Create Date: 2024-01-04 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default built-in categories to seed
_BUILTIN = [
    ("hardware",  "Hardware",      "#8B5CF6", 1),
    ("software",  "Software",      "#3B82F6", 2),
    ("network",   "Network",       "#10B981", 3),
    ("access",    "Access",        "#F59E0B", 4),
    ("email",     "Email",         "#EF4444", 5),
    ("security",  "Security",      "#EC4899", 6),
    ("other",     "Other",         "#6B7280", 7),
]


def upgrade() -> None:
    # ── 1. Create categories table ──────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug",        sa.String(80),  nullable=False, unique=True),
        sa.Column("name",        sa.String(100), nullable=False),
        sa.Column("color",       sa.String(7),   nullable=False, server_default="#6B7280"),
        sa.Column("description", sa.Text,        nullable=True),
        sa.Column("is_builtin",  sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("sort_order",  sa.Integer,     nullable=False, server_default="100"),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_categories_slug", "categories", ["slug"], unique=True)

    # ── 2. Seed built-in categories ─────────────────────────────────────────
    for slug, name, color, order in _BUILTIN:
        op.execute(
            f"INSERT INTO categories (id, slug, name, color, is_builtin, sort_order) "
            f"VALUES (gen_random_uuid(), '{slug}', '{name}', '{color}', TRUE, {order})"
        )

    # ── 3. Change tickets.category: enum → varchar ──────────────────────────
    op.execute(
        "ALTER TABLE tickets "
        "ALTER COLUMN category TYPE VARCHAR(80) USING category::TEXT"
    )
    op.create_index("ix_tickets_category", "tickets", ["category"])

    # ── 4. Change inbound_email_config.default_category: enum → varchar ─────
    op.execute(
        "ALTER TABLE inbound_email_config "
        "ALTER COLUMN default_category TYPE VARCHAR(80) USING default_category::TEXT"
    )
    # Set default value for email-triggered tickets
    op.execute(
        "UPDATE inbound_email_config SET default_category = 'email'"
    )

    # ── 5. Drop the old ticketcategory enum ─────────────────────────────────
    # (Must happen after all columns referencing it are converted)
    op.execute("DROP TYPE IF EXISTS ticketcategory")


def downgrade() -> None:
    # Re-create the enum and convert back
    op.execute(
        "CREATE TYPE ticketcategory AS ENUM "
        "('hardware','software','network','access','email','security','other')"
    )
    op.execute(
        "ALTER TABLE tickets "
        "ALTER COLUMN category TYPE ticketcategory "
        "USING CASE WHEN category IN ('hardware','software','network','access','email','security','other') "
        "THEN category::ticketcategory ELSE 'other'::ticketcategory END"
    )
    op.execute(
        "ALTER TABLE inbound_email_config "
        "ALTER COLUMN default_category TYPE ticketcategory "
        "USING 'other'::ticketcategory"
    )
    op.drop_index("ix_tickets_category", table_name="tickets")
    op.drop_index("ix_categories_slug", table_name="categories")
    op.drop_table("categories")
