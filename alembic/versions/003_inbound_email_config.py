"""add inbound email config and email ticket log tables

Revision ID: 003
Revises: 002
Create Date: 2024-01-03 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE inboundauthtype AS ENUM ('basic', 'oauth', 'graph')")
    op.execute("CREATE TYPE emaillogstatus AS ENUM ('processed', 'duplicate', 'error')")

    # inbound_email_config
    op.create_table(
        "inbound_email_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("auth_type",
            sa.Enum("basic", "oauth", "graph", name="inboundauthtype"),
            nullable=False, server_default="basic"),
        # IMAP
        sa.Column("imap_host",   sa.String(255), nullable=True),
        sa.Column("imap_port",   sa.Integer,     nullable=False, server_default="993"),
        sa.Column("imap_ssl",    sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("imap_user",   sa.String(255), nullable=True),
        sa.Column("imap_pass",   sa.Text,        nullable=True),
        sa.Column("imap_folder", sa.String(100), nullable=False, server_default="'INBOX'"),
        # Graph
        sa.Column("graph_mailbox", sa.String(255), nullable=True),
        # Auto-ticket defaults
        sa.Column("default_category",
            sa.Enum("hardware","software","network","access","email","security","other",
                    name="ticketcategory"),
            nullable=False, server_default="'other'"),
        sa.Column("default_priority",
            sa.Enum("critical","high","medium","low", name="ticketpriority"),
            nullable=False, server_default="'medium'"),
        sa.Column("default_assignee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True),
        # Polling
        sa.Column("poll_interval_minutes", sa.Integer, nullable=False, server_default="5"),
        sa.Column("mark_seen",       sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("move_to_folder",  sa.String(100), nullable=True),
        # Stats
        sa.Column("last_polled_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at",      sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # email_ticket_log
    op.create_table(
        "email_ticket_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("inbound_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inbound_email_config.id", ondelete="CASCADE"),
            nullable=False),
        sa.Column("message_id",  sa.String(512), nullable=False, unique=True),
        sa.Column("from_email",  sa.String(255), nullable=False),
        sa.Column("from_name",   sa.String(100), nullable=False, server_default="''"),
        sa.Column("subject",     sa.String(512), nullable=False, server_default="''"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status",
            sa.Enum("processed", "duplicate", "error", name="emaillogstatus"),
            nullable=False, server_default="'processed'"),
        sa.Column("ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="SET NULL"),
            nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("processed_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_email_ticket_log_inbound",      "email_ticket_log", ["inbound_config_id"])
    op.create_index("ix_email_ticket_log_message_id",   "email_ticket_log", ["message_id"], unique=True)
    op.create_index("ix_email_ticket_log_processed_at", "email_ticket_log", ["processed_at"])


def downgrade() -> None:
    op.drop_table("email_ticket_log")
    op.drop_table("inbound_email_config")
    op.execute("DROP TYPE IF EXISTS emaillogstatus")
    op.execute("DROP TYPE IF EXISTS inboundauthtype")
