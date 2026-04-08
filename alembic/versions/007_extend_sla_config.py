"""extend sla_config with timer_start, countdown_mode, business hours, pause_on

Revision ID: 007
Revises: 006
Create Date: 2026-04-07 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL ADD COLUMN IF NOT EXISTS for idempotency.
    # Safe to re-run even if these columns already exist (edge-case replays).
    op.execute("ALTER TABLE sla_config ADD COLUMN IF NOT EXISTS timer_start    VARCHAR(20) NOT NULL DEFAULT 'on_creation'")
    op.execute("ALTER TABLE sla_config ADD COLUMN IF NOT EXISTS countdown_mode VARCHAR(20) NOT NULL DEFAULT '24_7'")
    op.execute("ALTER TABLE sla_config ADD COLUMN IF NOT EXISTS work_days      JSONB       NOT NULL DEFAULT '[0,1,2,3,4]'")
    op.execute("ALTER TABLE sla_config ADD COLUMN IF NOT EXISTS work_start     VARCHAR(5)  NOT NULL DEFAULT '09:00'")
    op.execute("ALTER TABLE sla_config ADD COLUMN IF NOT EXISTS work_end       VARCHAR(5)  NOT NULL DEFAULT '20:00'")
    op.execute("""ALTER TABLE sla_config ADD COLUMN IF NOT EXISTS pause_on JSONB NOT NULL DEFAULT '["on-hold"]'""")


def downgrade() -> None:
    op.drop_column("sla_config", "pause_on")
    op.drop_column("sla_config", "work_end")
    op.drop_column("sla_config", "work_start")
    op.drop_column("sla_config", "work_days")
    op.drop_column("sla_config", "countdown_mode")
    op.drop_column("sla_config", "timer_start")
