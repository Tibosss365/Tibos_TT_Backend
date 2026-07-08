"""
ORM models for the agent-facing email inbox (migration 036).
  - EmailAccount      – a connected mailbox (IMAP/SMTP or MS Graph)
  - EmailThread       – a conversation grouping of messages
  - EmailMessage      – a single inbound/outbound email
  - InboxEmailTemplate – reusable reply templates with {{variables}}
  - EmailSignature    – per-agent signatures
  - EmailRoutingRule  – conditions + actions applied to fetched mail

String columns are used instead of PG enums on purpose: the frontend
types are plain string unions, and varchar avoids the enum/varchar
drift that has bitten this schema before (see migration 035).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email_address: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # imap_smtp | graph_api
    protocol: Mapped[str] = mapped_column(String(20), nullable=False, default="imap_smtp", server_default="imap_smtp")

    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993, server_default="993")
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    imap_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_password: Mapped[str | None] = mapped_column(Text, nullable=True)

    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=587, server_default="587")
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True)

    graph_tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    graph_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    graph_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    auto_create_tickets: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    default_ticket_priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium", server_default="medium")
    default_assign_team_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class EmailThread(Base):
    __tablename__ = "email_threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    snippet: Mapped[str | None] = mapped_column(String(300), nullable=True)
    participant_emails: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_spam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_trashed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RFC Message-ID header for dedupe / threading
    rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # inbound | outbound
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="inbound", server_default="inbound")
    # original | reply | forward | internal_note
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, default="original", server_default="original")
    from_email: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    to_recipients: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    cc_recipients: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    bcc_recipients: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_stripped: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | queued | sent | delivered | failed | bounced
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False, default="delivered", server_default="delivered")
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_opened: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_suggested_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    # positive | neutral | negative
    ai_sentiment: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # [{id, filename, content_type, size_bytes, content_id, is_inline, storage_path}]
    attachments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class InboxEmailTemplate(Base):
    __tablename__ = "inbox_email_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="general", server_default="general")
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{name, description, default}]
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class EmailSignature(Base):
    __tablename__ = "email_signatures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class EmailRoutingRule(Base):
    __tablename__ = "email_routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # [{field, operator, value}]
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    # AND | OR
    condition_logic: Mapped[str] = mapped_column(String(3), nullable=False, default="AND", server_default="AND")
    # [{type, params}]
    actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="'[]'::jsonb")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
