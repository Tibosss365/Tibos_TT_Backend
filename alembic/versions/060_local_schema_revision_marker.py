"""Recognize databases already recorded at revision 060.

Revision ID: 060
Revises: 059

The local database is already stamped at 060, but this checkout contains no
schema delta for that revision. Keep this marker as a no-op so Alembic can
validate the existing database revision and continue to later migrations.
"""

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
