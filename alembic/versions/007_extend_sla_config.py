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
    op.add_column("sla_config", sa.Column("timer_start",    sa.String(20),  nullable=False, server_default="on_creation"))
    op.add_column("sla_config", sa.Column("countdown_mode", sa.String(20),  nullable=False, server_default="24_7"))
    op.add_column("sla_config", sa.Column("work_days",      JSONB(),        nullable=False, server_default="[0,1,2,3,4]"))
    op.add_column("sla_config", sa.Column("work_start",     sa.String(5),   nullable=False, server_default="'09:00'"))
    op.add_column("sla_config", sa.Column("work_end",       sa.String(5),   nullable=False, server_default="'20:00'"))
    op.add_column("sla_config", sa.Column("pause_on",       JSONB(),        nullable=False, server_default='["on-hold"]'))


def downgrade() -> None:
    op.drop_column("sla_config", "pause_on")
    op.drop_column("sla_config", "work_end")
    op.drop_column("sla_config", "work_start")
    op.drop_column("sla_config", "work_days")
    op.drop_column("sla_config", "countdown_mode")
    op.drop_column("sla_config", "timer_start")
