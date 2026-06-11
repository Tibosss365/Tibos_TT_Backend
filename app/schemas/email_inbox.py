"""
Pydantic schemas for the agent-facing email inbox (/email API).
Field names and shapes mirror the frontend contract in
Tibos_TT_Frontend/src/types/email.ts — change both together.
"""
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field, field_serializer


def _utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class _OutBase(BaseModel):
    model_config = {"from_attributes": True}


# ── Recipient ─────────────────────────────────────────────────────────────────

class Recipient(BaseModel):
    email: EmailStr
    name: str | None = None


# ── Accounts ──────────────────────────────────────────────────────────────────

class EmailAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email_address: EmailStr
    display_name: str | None = Field(default=None, max_length=120)
    protocol: str = Field(default="imap_smtp", pattern="^(imap_smtp|graph_api)$")
    imap_host: str | None = None
    imap_port: int = 993
    imap_use_ssl: bool = True
    imap_username: str | None = None
    imap_password: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: str | None = None
    smtp_password: str | None = None
    graph_tenant_id: str | None = None
    graph_client_id: str | None = None
    graph_client_secret: str | None = None
    graph_user_id: str | None = None
    auto_create_tickets: bool = False
    default_ticket_priority: str = "medium"
    default_assign_team_id: str | None = None


class EmailAccountUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    is_default: bool | None = None
    auto_create_tickets: bool | None = None
    default_ticket_priority: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_use_ssl: bool | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    graph_client_secret: str | None = None


class EmailAccountOut(_OutBase):
    """Credentials are intentionally excluded from responses."""
    id: uuid.UUID
    name: str
    email_address: str
    display_name: str | None
    protocol: str
    is_active: bool
    is_default: bool
    auto_create_tickets: bool
    default_ticket_priority: str
    last_fetched_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_fetched_at", "created_at", "updated_at", when_used="json")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class FetchResult(BaseModel):
    fetched: int


# ── Threads ───────────────────────────────────────────────────────────────────

class EmailThreadOut(_OutBase):
    id: uuid.UUID
    account_id: uuid.UUID
    ticket_id: uuid.UUID | None
    subject: str
    snippet: str | None
    participant_emails: list[str]
    is_read: bool
    is_starred: bool
    is_archived: bool
    is_spam: bool
    message_count: int
    unread_count: int
    has_attachments: bool
    last_message_at: datetime
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_message_at", "created_at", "updated_at", when_used="json")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class EmailThreadUpdate(BaseModel):
    is_read: bool | None = None
    is_starred: bool | None = None
    is_archived: bool | None = None
    is_spam: bool | None = None
    ticket_id: uuid.UUID | None = None


class PaginatedThreads(BaseModel):
    items: list[EmailThreadOut]
    total: int
    page: int
    pages: int


class LinkTicketRequest(BaseModel):
    ticket_id: uuid.UUID | None = None


class MarkReadRequest(BaseModel):
    message_ids: list[uuid.UUID] = []
    is_read: bool = True


# ── Messages ──────────────────────────────────────────────────────────────────

class EmailAttachmentOut(BaseModel):
    id: str
    filename: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    content_id: str | None = None
    is_inline: bool = False
    storage_path: str | None = None


class EmailMessageOut(_OutBase):
    id: uuid.UUID
    thread_id: uuid.UUID
    account_id: uuid.UUID
    direction: str
    message_type: str
    from_email: str
    from_name: str | None
    sent_by_agent_id: uuid.UUID | None
    to_recipients: list[Recipient]
    cc_recipients: list[Recipient]
    bcc_recipients: list[Recipient]
    subject: str | None
    body_html: str | None
    body_text: str | None
    body_stripped: str | None
    delivery_status: str
    delivery_error: str | None
    sent_at: datetime | None
    is_read: bool
    read_at: datetime | None
    is_opened: bool
    open_count: int
    first_opened_at: datetime | None
    ai_summary: str | None
    ai_suggested_reply: str | None
    ai_sentiment: str | None
    received_at: datetime
    attachments: list[EmailAttachmentOut]

    @field_serializer(
        "sent_at", "read_at", "first_opened_at", "received_at", when_used="json"
    )
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class SendEmailRequest(BaseModel):
    thread_id: uuid.UUID | None = None
    account_id: uuid.UUID
    to: list[Recipient] = Field(..., min_length=1)
    cc: list[Recipient] = []
    bcc: list[Recipient] = []
    subject: str = Field(..., max_length=500)
    body_html: str
    body_text: str | None = None
    message_type: str = Field(default="reply", pattern="^(reply|internal_note|forward|original)$")
    in_reply_to_message_id: uuid.UUID | None = None
    signature_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None


class ForwardRequest(BaseModel):
    to: list[Recipient] = Field(..., min_length=1)
    cc: list[Recipient] = []
    additional_note: str | None = None


# ── Templates ─────────────────────────────────────────────────────────────────

class TemplateVariable(BaseModel):
    name: str
    description: str | None = None
    default: str | None = None


class EmailTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    category: str = Field(default="general", max_length=80)
    subject: str = Field(..., max_length=500)
    body_html: str
    body_text: str | None = None
    variables: list[TemplateVariable] = []
    is_shared: bool = True


class EmailTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    subject: str | None = Field(default=None, max_length=500)
    body_html: str | None = None
    variables: list[TemplateVariable] | None = None
    is_active: bool | None = None
    is_shared: bool | None = None


class EmailTemplateOut(_OutBase):
    id: uuid.UUID
    name: str
    category: str
    subject: str
    body_html: str
    body_text: str | None
    variables: list[TemplateVariable]
    is_active: bool
    is_shared: bool
    use_count: int
    created_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class TemplateRenderRequest(BaseModel):
    template_id: uuid.UUID
    variables: dict[str, str] = {}


class TemplateRenderOut(BaseModel):
    subject: str
    body_html: str
    body_text: str | None = None


# ── Signatures ────────────────────────────────────────────────────────────────

class EmailSignatureCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    body_html: str
    body_text: str | None = None
    is_default: bool = False


class EmailSignatureUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    body_html: str | None = None
    is_default: bool | None = None


class EmailSignatureOut(_OutBase):
    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    body_html: str
    body_text: str | None
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


# ── Routing rules ─────────────────────────────────────────────────────────────

class RoutingCondition(BaseModel):
    field: str
    operator: str
    value: str


class RoutingAction(BaseModel):
    type: str
    params: dict = {}


class EmailRoutingRuleCreate(BaseModel):
    account_id: uuid.UUID | None = None
    name: str = Field(..., min_length=1, max_length=120)
    priority: int = 0
    conditions: list[RoutingCondition] = []
    condition_logic: str = Field(default="AND", pattern="^(AND|OR)$")
    actions: list[RoutingAction] = []


class EmailRoutingRuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    priority: int | None = None
    is_active: bool | None = None
    conditions: list[RoutingCondition] | None = None
    condition_logic: str | None = Field(default=None, pattern="^(AND|OR)$")
    actions: list[RoutingAction] | None = None


class EmailRoutingRuleOut(_OutBase):
    id: uuid.UUID
    account_id: uuid.UUID | None
    name: str
    priority: int
    is_active: bool
    conditions: list[RoutingCondition]
    condition_logic: str
    actions: list[RoutingAction]
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


# ── AI ────────────────────────────────────────────────────────────────────────

class AISuggestRequest(BaseModel):
    message_id: uuid.UUID
    tone: str = "professional"
    language: str = "en"
    max_length: int | None = None


class AISuggestOut(BaseModel):
    suggestion: str
    tone: str


class AISummarizeRequest(BaseModel):
    thread_id: uuid.UUID
    max_length: int | None = None


class AISummarizeOut(BaseModel):
    summary: str
    sentiment: str | None = None
    key_points: list[str] = []
