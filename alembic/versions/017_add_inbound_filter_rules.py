"""Add filter_rules to inbound_email_config and 'filtered' to emaillogstatus

Revision ID: 017
Revises: 016
Create Date: 2026-04-18 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL enum ADD VALUE cannot run inside a transaction block.
    # op.execute() with autocommit=False is fine; Alembic handles this.
    op.execute("ALTER TYPE emaillogstatus ADD VALUE IF NOT EXISTS 'filtered'")

    op.add_column(
        "inbound_email_config",
        sa.Column(
            "filter_rules",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("inbound_email_config", "filter_rules")
    # Note: PostgreSQL does not support removing enum values.
    # The 'filtered' value remains in the type after downgrade.
