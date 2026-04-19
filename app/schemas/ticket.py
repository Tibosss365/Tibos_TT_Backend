import uuid
from datetime import datetime, timezone
from typing import Union
from pydantic import BaseModel, Field, computed_field, field_serializer

from app.models.ticket import SLAStatus, TicketCategory, TicketPriority, TicketStatus, TimelineType
from app.schemas.user import UserPublic


def _utc_iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO-8601 with explicit UTC offset.

    Naive datetimes from the DB are assumed to be UTC and tagged accordingly
    so that JavaScript ``new Date()`` always interprets them as UTC, not local
    time — preventing the "+5:30 IST offset" display bug.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class TimelineEntryOut(BaseModel):
    id: uuid.UUID
    type: TimelineType
    text: str
    author: UserPublic | None = None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser_created_at(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


class TicketBase(BaseModel):
    subject: str = Field(..., min_length=1, max_length=255)
    category: str = Field(..., min_length=1, max_length=80)
    priority: TicketPriority = TicketPriority.medium
    company: str = Field(default="")
    contact_name: str = Field(default="")
    email: str = Field(default="")
    phone: str | None = None
    asset: str | None = None
    description: str = Field(..., min_length=1)
    submitter_name: str = Field(..., min_length=1, max_length=100)


class TicketCreate(TicketBase):
    assignee_id: uuid.UUID | None = None
    group_id:str | None=None


class TicketUpdate(BaseModel):
    subject: str | None = None
    category: str | None = Field(default=None, max_length=80)
    priority: TicketPriority | None = None
    status: TicketStatus | None = None
    assignee_id: uuid.UUID | None = None
    company: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    asset: str | None = None
    description: str | None = None
    resolution: str | None = None
    group_id:str | None=None


class TicketOut(TicketBase):
    id: uuid.UUID
    ticket_number: int
    ticket_id: str
    group_id: str | None = None   # nullable — not all tickets belong to a group
    status: TicketStatus
    assignee_id: uuid.UUID | None
    assignee: UserPublic | None = None
    resolution: str | None = None
    # ── SLA fields ──────────────────────────────────────────────────────
    sla_status: SLAStatus = SLAStatus.not_started
    sla_start_time: datetime | None = None
    sla_due_time: datetime | None = None
    sla_paused_at: datetime | None = None
    sla_paused_seconds: int = 0
    # Legacy (kept for backward compat)
    sla_due_at: datetime | None = None
    timeline: list[TimelineEntryOut] = []
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "sla_start_time", "sla_due_time", "sla_paused_at", "sla_due_at",
        "created_at", "updated_at",
        when_used="json",
    )
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    @computed_field
    @property
    def is_overdue(self) -> bool:
        """True if SLA is active/overdue and past its due time."""
        if self.sla_status in (SLAStatus.completed, SLAStatus.not_started, SLAStatus.paused):
            return False
        if self.sla_status == SLAStatus.overdue:
            return True
        # active: check wall clock
        due = self.sla_due_time or self.sla_due_at
        if due is None:
            return False
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > due

    model_config = {"from_attributes": True}


class TicketListOut(BaseModel):
    """Lightweight ticket response for list views (no timeline)."""
    id: uuid.UUID
    ticket_number: int
    ticket_id: str
    subject: str
    category: str
    priority: TicketPriority
    status: TicketStatus
    submitter_name: str
    company: str
    resolution: str | None = None
    # group + assignee — required by normalizeTicket() and dashboard group/agent filter
    group_id: str | None = None
    assignee_id: uuid.UUID | None = None
    assignee: Union[UserPublic, None] | None = None
    # ── SLA fields ──────────────────────────────────────────────────────
    sla_status: Union[SLAStatus, str, None] = SLAStatus.not_started
    sla_start_time: Union[datetime, None] | None = None
    sla_due_time: Union[datetime, None] | None = None
    sla_paused_at: Union[datetime, None] | None = None
    sla_paused_seconds: Union[int, None] = 0
    sla_due_at: Union[datetime, None] | None = None  # legacy
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "sla_start_time", "sla_due_time", "sla_paused_at", "sla_due_at",
        "created_at", "updated_at",
        when_used="json",
    )
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    @computed_field
    @property
    def is_overdue(self) -> bool:
        if self.sla_status in (SLAStatus.completed, SLAStatus.not_started, SLAStatus.paused):
            return False
        if self.sla_status == SLAStatus.overdue:
            return True
        due = self.sla_due_time or self.sla_due_at
        if due is None:
            return False
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > due

    model_config = {"from_attributes": True}


class TicketFilter(BaseModel):
    search: str | None = None
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category: str | None = None
    assignee_id: uuid.UUID | None = None
    sort: str = "newest"  # newest | oldest | priority | updated


class BulkTicketAction(BaseModel):
    ticket_ids: list[uuid.UUID] = Field(..., min_length=1)
    action: str = Field(..., pattern="^(resolve|close|delete)$")


class AddCommentRequest(BaseModel):
    text: str = Field(..., min_length=1)
    send_to_customer: bool = False


class PaginatedTickets(BaseModel):
    items: list[TicketListOut]
    total: int
    page: int
    page_size: int
    pages: int
