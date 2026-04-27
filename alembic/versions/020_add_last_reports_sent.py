"""Add last_reports_sent column to alert_settings

Revision ID: 020
Revises: 019
Create Date: 2026-04-21 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE alert_settings ADD COLUMN IF NOT EXISTS last_reports_sent JSONB")


def downgrade() -> None:
    op.drop_column("alert_settings", "last_reports_sent")
