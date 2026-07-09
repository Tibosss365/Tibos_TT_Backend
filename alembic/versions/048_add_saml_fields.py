"""Add SAML fields to sso_config

Revision ID: 048
Revises: 047
Create Date: 2026-07-09

Adds three SAML 2.0 columns to the sso_config singleton table:
  saml_mode        - toggle OIDC vs SAML protocol
  idp_metadata_url - Azure AD federation metadata URL (auto-fetch IdP cert)
  idp_cert         - PEM X.509 certificate from the identity provider
"""
from alembic import op
import sqlalchemy as sa

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sso_config",
        sa.Column("saml_mode", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "sso_config",
        sa.Column("idp_metadata_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "sso_config",
        sa.Column("idp_cert", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sso_config", "idp_cert")
    op.drop_column("sso_config", "idp_metadata_url")
    op.drop_column("sso_config", "saml_mode")
