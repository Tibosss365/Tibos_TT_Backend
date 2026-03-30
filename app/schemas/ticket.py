import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.ticket import TicketCategory, TicketPriority, TicketStatus, TimelineType
from app.schemas.user import UserPublic


class TimelineEntryOut(BaseModel):
    id: uuid.UUID
    type: TimelineType
    text: str
    author: UserPublic | None = None
    created_at: datetime

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


class TicketOut(TicketBase):
    id: uuid.UUID
    ticket_number: int
    ticket_id: str
    status: TicketStatus
    assignee_id: uuid.UUID | None
    assignee: UserPublic | None = None
    timeline: list[TimelineEntryOut] = []
    created_at: datetime
    updated_at: datetime

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
    assignee: UserPublic | None = None
    created_at: datetime
    updated_at: datetime

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


class PaginatedTickets(BaseModel):
    items: list[TicketListOut]
    total: int
    page: int
    page_size: int
    pages: int
