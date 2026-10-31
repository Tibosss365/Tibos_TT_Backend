"""Add ticket type to saved ticket templates.

Revision ID: 061
Revises: 060
"""
from alembic import op
import sqlalchemy as sa


revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticket_templates",
        sa.Column("type", sa.String(length=30), nullable=False, server_default="request"),
    )


def downgrade() -> None:
    op.drop_column("ticket_templates", "type")
