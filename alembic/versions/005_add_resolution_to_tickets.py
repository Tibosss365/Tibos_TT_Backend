"""add resolution column to tickets

Revision ID: 005
Revises: 004
Create Date: 2026-04-07 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("resolution", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "resolution")
