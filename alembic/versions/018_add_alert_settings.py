"""Add alert_settings table

Revision ID: 018
Revises: 017
Create Date: 2026-04-20 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEFAULT_CONDITIONS = {
    "unassigned":  {"enabled": True,  "thresholdMins": 30},
    "slaBreach":   {"enabled": True,  "includeWarning": True},
    "openToday":   {"enabled": False},
    "onHold":      {"enabled": False, "thresholdHours": 24},
    "inProgress":  {"enabled": False, "thresholdHours": 48},
}

_DEFAULT_REPORTS = {
    "daily":   {"enabled": False, "time": "08:00"},
    "weekly":  {"enabled": False, "day": "monday",  "time": "08:00"},
    "monthly": {"enabled": False, "dayOfMonth": 1,  "time": "08:00"},
}

_DEFAULT_RECIPIENTS = {
    "includeAdmin": True,
    "emails": [],
}


def upgrade() -> None:
    # Create alert_settings table (idempotent)
    op.execute("""
        CREATE TABLE IF NOT EXISTS alert_settings (
            id         SERIAL PRIMARY KEY,
            conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
            reports    JSONB NOT NULL DEFAULT '{}'::jsonb,
            recipients JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    import json
    op.execute(
        f"""
        INSERT INTO alert_settings (id, conditions, reports, recipients, updated_at)
        VALUES (
            1,
            '{json.dumps(_DEFAULT_CONDITIONS)}'::jsonb,
            '{json.dumps(_DEFAULT_REPORTS)}'::jsonb,
            '{json.dumps(_DEFAULT_RECIPIENTS)}'::jsonb,
            now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("alert_settings")
