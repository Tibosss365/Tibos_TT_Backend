"""Add 'none' value to smtpsecurity enum

The SMTP outbound form has a 'None / Plain' option. Without this enum value
the backend throws a 422 when the admin saves with no encryption selected.

Revision ID: 014
Revises: 013
Create Date: 2026-04-16 00:00:00
"""
from typing import Sequence, Union
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL requires ALTER TYPE to add enum values; idempotent via DO block
    op.execute("""
        DO $$ BEGIN
            ALTER TYPE smtpsecurity ADD VALUE IF NOT EXISTS 'none';
        EXCEPTION WHEN others THEN null;
        END $$
    """)


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    # Safe to leave 'none' in place — it simply won't be used after downgrade.
    pass
