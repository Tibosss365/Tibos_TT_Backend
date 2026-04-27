"""Backfill IST timezone for reports and email config

Revision ID: 022
Revises: 021
Create Date: 2026-04-27

Sets Asia/Kolkata as the timezone for:
  - alert_settings.reports JSON (adds top-level "timezone" key if missing)
  - email_config.trigger_timezone (updates rows still set to "UTC")
"""
from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Add timezone key to alert_settings.reports JSON where it is absent
    conn.execute(sa.text(
        """
        UPDATE alert_settings
        SET reports = reports || '{"timezone": "Asia/Kolkata"}'::jsonb
        WHERE NOT (reports ? 'timezone')
        """
    ))

    # Update email_config rows whose trigger_timezone is still the old default "UTC"
    conn.execute(sa.text(
        """
        UPDATE email_config
        SET trigger_timezone = 'Asia/Kolkata'
        WHERE trigger_timezone = 'UTC'
        """
    ))

    # Also update the column server_default for future rows
    op.alter_column(
        "email_config",
        "trigger_timezone",
        server_default="Asia/Kolkata",
        existing_type=sa.String(length=50),
        existing_nullable=False,
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text(
        """
        UPDATE email_config
        SET trigger_timezone = 'UTC'
        WHERE trigger_timezone = 'Asia/Kolkata'
        """
    ))

    op.alter_column(
        "email_config",
        "trigger_timezone",
        server_default="UTC",
        existing_type=sa.String(length=50),
        existing_nullable=False,
    )
