"""add oauth fields to email_config

Revision ID: 002
Revises: a99ac67b301a
Create Date: 2024-01-02 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "a99ac67b301a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE oauthprovider AS ENUM ('google', 'microsoft', 'custom')")

    op.add_column("email_config", sa.Column("oauth_provider",
        sa.Enum("google", "microsoft", "custom", name="oauthprovider"), nullable=True))
    op.add_column("email_config", sa.Column("oauth_client_id",     sa.String(255), nullable=True))
    op.add_column("email_config", sa.Column("oauth_client_secret", sa.Text,        nullable=True))
    op.add_column("email_config", sa.Column("oauth_redirect_uri",  sa.String(512), nullable=True))
    op.add_column("email_config", sa.Column("oauth_scopes",        sa.Text,        nullable=True))
    op.add_column("email_config", sa.Column("oauth_auth_endpoint", sa.String(512), nullable=True))
    op.add_column("email_config", sa.Column("oauth_token_endpoint",sa.String(512), nullable=True))
    op.add_column("email_config", sa.Column("oauth_from",          sa.String(255), nullable=True))
    op.add_column("email_config", sa.Column("oauth_access_token",  sa.Text,        nullable=True))
    op.add_column("email_config", sa.Column("oauth_refresh_token", sa.Text,        nullable=True))
    op.add_column("email_config", sa.Column("oauth_token_expiry",
        sa.DateTime(timezone=True), nullable=True))

    # Add 'oauth' to the emailtype enum
    op.execute("ALTER TYPE emailtype ADD VALUE IF NOT EXISTS 'oauth'")


def downgrade() -> None:
    for col in [
        "oauth_provider", "oauth_client_id", "oauth_client_secret",
        "oauth_redirect_uri", "oauth_scopes", "oauth_auth_endpoint",
        "oauth_token_endpoint", "oauth_from", "oauth_access_token",
        "oauth_refresh_token", "oauth_token_expiry",
    ]:
        op.drop_column("email_config", col)

    op.execute("DROP TYPE IF EXISTS oauthprovider")
