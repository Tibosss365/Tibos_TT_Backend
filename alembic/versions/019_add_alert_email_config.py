"""Add alert_email_config column to alert_settings

Revision ID: 019
Revises: 018
Create Date: 2026-04-20 01:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_EMAIL_CFG = (
    '{"useSameAsEmail": true, "type": "smtp", '
    '"smtp": {"host": "", "port": "587", "security": "tls", "from": "", "user": "", "pass": ""}, '
    '"m365": {"tenantId": "", "clientId": "", "clientSecret": "", "from": ""}}'
)


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE alert_settings ADD COLUMN IF NOT EXISTS "
        f"alert_email_config JSONB DEFAULT '{_DEFAULT_EMAIL_CFG}'::jsonb"
    )


def downgrade() -> None:
    op.drop_column("alert_settings", "alert_email_config")
