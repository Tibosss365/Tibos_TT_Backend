import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.inbound_email import InboundAuthType, EmailLogStatus
from app.models.ticket import TicketCategory, TicketPriority


class InboundEmailConfigOut(BaseModel):
    id: uuid.UUID
    enabled: bool
    auth_type: InboundAuthType
    imap_host: str | None
    imap_port: int
    imap_ssl: bool
    imap_user: str | None
    imap_folder: str
    graph_mailbox: str | None
    default_category: TicketCategory
    default_priority: TicketPriority
    default_assignee_id: uuid.UUID | None
    poll_interval_minutes: int
    mark_seen: bool
    move_to_folder: str | None
    last_polled_at: datetime | None
    processed_count: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class InboundEmailConfigUpdate(BaseModel):
    enabled: bool | None = None
    auth_type: InboundAuthType | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_ssl: bool | None = None
    imap_user: str | None = None
    imap_pass: str | None = None      # write-only; not returned in Out
    imap_folder: str | None = None
    graph_mailbox: str | None = None
    default_category: TicketCategory | None = None
    default_priority: TicketPriority | None = None
    default_assignee_id: uuid.UUID | None = None
    poll_interval_minutes: int | None = Field(None, ge=1, le=1440)
    mark_seen: bool | None = None
    move_to_folder: str | None = None


class EmailTicketLogOut(BaseModel):
    id: uuid.UUID
    message_id: str
    from_email: str
    from_name: str
    subject: str
    received_at: datetime | None
    status: EmailLogStatus
    ticket_id: uuid.UUID | None
    ticket_number: str | None = None   # e.g. "TKT-0042", populated by router
    error_message: str | None
    processed_at: datetime

    model_config = {"from_attributes": True}


class EmailLogPage(BaseModel):
    items: list[EmailTicketLogOut]
    total: int


class PollResult(BaseModel):
    polled_at: str
    processed: int
    error: str | None
    duration_ms: int
