import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    String, Boolean, Integer, Text, DateTime, ForeignKey,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.ticket import TicketPriority


class InboundAuthType(str, enum.Enum):
    basic  = "basic"   # IMAP username + password
    oauth  = "oauth"   # XOAUTH2 using stored OAuth token (Gmail)
    graph  = "graph"   # Microsoft Graph API (M365 / Exchange Online)


class EmailLogStatus(str, enum.Enum):
    processed = "processed"
    duplicate = "duplicate"
    error     = "error"


class InboundEmailConfig(Base):
    """IMAP / Graph inbox-to-ticket configuration (single-row table)."""
    __tablename__ = "inbound_email_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # ── Feature flag ──────────────────────────────────────────────────
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Auth method ───────────────────────────────────────────────────
    auth_type: Mapped[InboundAuthType] = mapped_column(
        SAEnum(InboundAuthType, name="inboundauthtype"),
        nullable=False, default=InboundAuthType.basic,
    )

    # ── IMAP connection (basic + XOAUTH2) ─────────────────────────────
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int] = mapped_column(Integer, default=993, nullable=False)
    imap_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    imap_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_pass: Mapped[str | None] = mapped_column(Text, nullable=True)   # encrypted in prod
    imap_folder: Mapped[str] = mapped_column(String(100), default="INBOX", nullable=False)

    # ── Microsoft Graph (M365) ────────────────────────────────────────
    # Reuses oauth tokens from EmailConfig; just needs the mailbox address
    graph_mailbox: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Auto-ticket defaults ──────────────────────────────────────────
    # Plain category slug — matches Category.slug (supports custom categories)
    default_category: Mapped[str] = mapped_column(
        String(80), nullable=False, default="email",
    )
    default_priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(TicketPriority, name="ticketpriority"),
        nullable=False, default=TicketPriority.medium,
    )
    default_assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Polling config ────────────────────────────────────────────────
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    # After processing, mark messages as Seen and optionally move them
    mark_seen: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    move_to_folder: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Stats (updated by poller) ─────────────────────────────────────
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────
    email_logs: Mapped[list["EmailTicketLog"]] = relationship(
        "EmailTicketLog", back_populates="inbound_config", cascade="all, delete-orphan",
        order_by="EmailTicketLog.processed_at.desc()",
    )


class EmailTicketLog(Base):
    """One row per email processed → ticket created (or attempted)."""
    __tablename__ = "email_ticket_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inbound_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbound_email_config.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Email metadata
    message_id: Mapped[str] = mapped_column(
        String(512), nullable=False, index=True, unique=True
    )
    from_email: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(512), nullable=False, default="(no subject)")
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Processing result
    status: Mapped[EmailLogStatus] = mapped_column(
        SAEnum(EmailLogStatus, name="emaillogstatus"),
        nullable=False, default=EmailLogStatus.processed,
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False, index=True,
    )

    # Relationships
    inbound_config: Mapped["InboundEmailConfig"] = relationship(
        "InboundEmailConfig", back_populates="email_logs"
    )
    ticket: Mapped["Ticket | None"] = relationship("Ticket")  # type: ignore[name-defined]
