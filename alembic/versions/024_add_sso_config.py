"""Add sso_config table and SSO fields on users

Revision ID: 024
Revises: 023
Create Date: 2026-04-27

Creates sso_config (singleton row) and adds external_id / auth_provider
columns to the users table so Azure AD OID can be stored per account.
"""
from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── sso_config table ──────────────────────────────────────────────────────
    op.create_table(
        "sso_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="microsoft"),
        sa.Column("tenant_id", sa.String(length=255), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.String(length=512), nullable=True),
        sa.Column("authorization_endpoint", sa.String(length=512), nullable=True),
        sa.Column("token_endpoint", sa.String(length=512), nullable=True),
        sa.Column("jwks_uri", sa.String(length=512), nullable=True),
        sa.Column("issuer", sa.String(length=512), nullable=True),
        sa.Column("default_role", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("admin_group_ids", sa.JSON(), nullable=True),
        sa.Column("agent_group_ids", sa.JSON(), nullable=True),
        sa.Column("auto_create_users", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sync_on_login", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── users table additions ─────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("external_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("auth_provider", sa.String(length=50), nullable=False,
                  server_default="local"),
    )
    op.create_unique_constraint("uq_users_external_id", "users", ["external_id"])
    op.create_index("ix_users_external_id", "users", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_constraint("uq_users_external_id", "users", type_="unique")
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "external_id")
    op.drop_table("sso_config")
