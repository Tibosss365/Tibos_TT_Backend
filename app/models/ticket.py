import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    String,
    Text,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Sequence,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TicketCategory(str, enum.Enum):
    """
    Kept for backward-compat filtering constants.
    The DB column is now a plain VARCHAR(80) referencing Category.slug,
    so any custom slug added by admin also works.
    """
    hardware = "hardware"
    software = "software"
    network  = "network"
    access   = "access"
    email    = "email"
    security = "security"
    other    = "other"


class TicketPriority(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in-progress"
    on_hold = "on-hold"
    resolved = "resolved"
    closed = "closed"


class TimelineType(str, enum.Enum):
    created = "created"
    assign = "assign"
    status = "status"
    comment = "comment"
    resolved = "resolved"


# Sequence for auto-incrementing ticket numbers
ticket_number_seq = Sequence("ticket_number_seq", start=1)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_number: Mapped[int] = mapped_column(
        Integer,
        ticket_number_seq,
        server_default=ticket_number_seq.next_value(),
        unique=True,
        nullable=False,
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # Plain varchar slug — references Category.slug; supports any admin-created category
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="other", index=True)
    priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(TicketPriority, name="ticketpriority"), nullable=False, default=TicketPriority.medium
    )
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticketstatus"), nullable=False, default=TicketStatus.open
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    company: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    contact_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    asset: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    assignee: Mapped["User | None"] = relationship(  # type: ignore[name-defined]
        "User", back_populates="assigned_tickets", foreign_keys=[assignee_id]
    )
    timeline: Mapped[list["TicketTimeline"]] = relationship(
        "TicketTimeline",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketTimeline.created_at",
    )
    notifications: Mapped[list["Notification"]] = relationship(  # type: ignore[name-defined]
        "Notification", back_populates="ticket", cascade="all, delete-orphan"
    )

    @property
    def ticket_id(self) -> str:
        return f"TKT-{self.ticket_number:04d}"


class TicketTimeline(Base):
    __tablename__ = "ticket_timeline"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[TimelineType] = mapped_column(
        SAEnum(TimelineType, name="timelinetype"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="timeline")
    author: Mapped["User | None"] = relationship(  # type: ignore[name-defined]
        "User", back_populates="timeline_entries"
    )
