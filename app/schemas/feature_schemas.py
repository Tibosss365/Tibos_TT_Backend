"""
Pydantic schemas for feature models: custom fields, templates, automation,
webhooks, notification channels, assets, escalation rules, recurring tickets,
portal branding, and CSAT survey.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_serializer, HttpUrl


def _utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── Custom Fields ─────────────────────────────────────────────────────────────

class CustomFieldCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    field_type: str = Field(default="text", pattern="^(text|number|date|dropdown|checkbox|url)$")
    options: list[str] = []
    is_required: bool = False
    display_order: int = 0


class CustomFieldUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    field_type: str | None = Field(default=None, pattern="^(text|number|date|dropdown|checkbox|url)$")
    options: list[str] | None = None
    is_required: bool | None = None
    display_order: int | None = None


class CustomFieldOut(BaseModel):
    id: uuid.UUID
    name: str
    field_type: str
    options: list[str]
    is_required: bool
    display_order: int
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Ticket Templates ──────────────────────────────────────────────────────────

class TicketTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    subject: str = Field(default="", max_length=255)
    category: str = Field(default="other", max_length=80)
    priority: str = "medium"
    group_id: str | None = None
    description_template: str | None = None
    custom_field_data: dict = {}
    tags: list[str] = []


class TicketTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    subject: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=80)
    priority: str | None = None
    group_id: str | None = None
    description_template: str | None = None
    custom_field_data: dict | None = None
    tags: list[str] | None = None


class TicketTemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    subject: str
    category: str
    priority: str
    group_id: str | None
    description_template: str | None
    custom_field_data: dict
    tags: list[str]
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Automation Rules ──────────────────────────────────────────────────────────

class AutomationRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    is_active: bool = True
    trigger: str = Field(
        default="ticket_created",
        pattern="^(ticket_created|ticket_updated|comment_added|status_changed|sla_breach)$",
    )
    conditions: list[dict] = []
    actions: list[dict] = []
    run_order: int = 0


class AutomationRuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    trigger: str | None = None
    conditions: list[dict] | None = None
    actions: list[dict] | None = None
    run_order: int | None = None


class AutomationRuleOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    trigger: str
    conditions: list[dict]
    actions: list[dict]
    run_order: int
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Webhook Configs ───────────────────────────────────────────────────────────

class WebhookConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., max_length=512)
    secret: str | None = Field(default=None, max_length=128)
    events: list[str] = []
    is_active: bool = True


class WebhookConfigUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=512)
    secret: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


class WebhookConfigOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    # Secret is never returned in API responses
    events: list[str]
    is_active: bool
    last_triggered_at: datetime | None
    created_at: datetime

    @field_serializer("last_triggered_at", "created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Notification Channels ─────────────────────────────────────────────────────

class NotificationChannelCreate(BaseModel):
    channel_type: str = Field(default="slack", pattern="^(slack|teams|discord|generic)$")
    name: str = Field(..., min_length=1, max_length=100)
    webhook_url: str = Field(..., max_length=512)
    events: list[str] = []
    is_active: bool = True


class NotificationChannelUpdate(BaseModel):
    channel_type: str | None = None
    name: str | None = Field(default=None, max_length=100)
    webhook_url: str | None = Field(default=None, max_length=512)
    events: list[str] | None = None
    is_active: bool | None = None


class NotificationChannelOut(BaseModel):
    id: uuid.UUID
    channel_type: str
    name: str
    webhook_url: str
    events: list[str]
    is_active: bool
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Assets ────────────────────────────────────────────────────────────────────

class AssetCreate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=50)
    name: str = Field(..., min_length=1, max_length=150)
    type: str = Field(default="other", max_length=30)
    serial_number: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    specification: str | None = None
    os_version: str | None = Field(default=None, max_length=100)
    asset_number: str | None = Field(default=None, max_length=50)
    processor: str | None = Field(default=None, max_length=150)
    ram: str | None = Field(default=None, max_length=50)
    rom: str | None = Field(default=None, max_length=50)
    status: str = "active"
    assigned_to: uuid.UUID | None = None
    assigned_to_name: str | None = Field(default=None, max_length=150)
    assigned_to_email: str | None = Field(default=None, max_length=255)
    employee_code: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=150)
    purchase_date: str | None = None   # ISO date string YYYY-MM-DD
    warranty_expiry: str | None = None  # ISO date string YYYY-MM-DD
    notes: str | None = None
    adaptor_status: str = "not_provided"  # not_provided | provided | replaced


class AssetUpdate(BaseModel):
    asset_tag: str | None = Field(default=None, max_length=50)
    name: str | None = Field(default=None, max_length=150)
    type: str | None = Field(default=None, max_length=30)
    serial_number: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    brand: str | None = None
    specification: str | None = None
    os_version: str | None = None
    asset_number: str | None = None
    processor: str | None = None
    ram: str | None = None
    rom: str | None = None
    status: str | None = None
    assigned_to: uuid.UUID | None = None
    # Empty string clears the field (unassign)
    assigned_to_name: str | None = Field(default=None, max_length=150)
    assigned_to_email: str | None = Field(default=None, max_length=255)
    employee_code: str | None = Field(default=None, max_length=50)
    location: str | None = None
    purchase_date: str | None = None
    warranty_expiry: str | None = None
    notes: str | None = None
    adaptor_status: str | None = None


class AssetOut(BaseModel):
    id: uuid.UUID
    asset_tag: str | None
    name: str
    type: str
    serial_number: str | None
    manufacturer: str | None
    model: str | None
    brand: str | None
    specification: str | None
    os_version: str | None
    asset_number: str | None
    processor: str | None
    ram: str | None
    rom: str | None
    status: str
    assigned_to: uuid.UUID | None
    assigned_to_name: str | None
    assigned_to_email: str | None
    employee_code: str | None
    location: str | None
    purchase_date: Any | None  # date or None
    warranty_expiry: Any | None
    notes: str | None
    adaptor_status: str = "not_provided"
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


class AssetHistoryOut(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    action: str
    assigned_to_name: str | None
    assigned_to_email: str | None
    employee_code: str | None
    note: str | None
    changed_by_name: str | None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Escalation Rules ──────────────────────────────────────────────────────────

class EscalationRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    is_active: bool = True
    priority: str = "high"
    hours_before_escalation: int = Field(default=4, ge=1)
    escalate_to_ids: list[str] = []  # list of user UUID strings
    notify_email: str | None = Field(default=None, max_length=255)


class EscalationRuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    priority: str | None = None
    hours_before_escalation: int | None = Field(default=None, ge=1)
    escalate_to_ids: list[str] | None = None
    notify_email: str | None = None


class EscalationRuleOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    priority: str
    hours_before_escalation: int
    escalate_to_ids: list[str]
    notify_email: str | None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Recurring Ticket Templates ────────────────────────────────────────────────

class RecurringTicketTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    cron_expr: str = Field(..., max_length=100)
    subject: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="other", max_length=80)
    priority: str = "medium"
    description: str | None = None
    assignee_id: uuid.UUID | None = None
    group_id: str | None = None
    is_active: bool = True


class RecurringTicketTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    cron_expr: str | None = Field(default=None, max_length=100)
    subject: str | None = Field(default=None, max_length=255)
    category: str | None = None
    priority: str | None = None
    description: str | None = None
    assignee_id: uuid.UUID | None = None
    group_id: str | None = None
    is_active: bool | None = None


class RecurringTicketTemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    cron_expr: str
    subject: str
    category: str
    priority: str
    description: str | None
    assignee_id: uuid.UUID | None
    group_id: str | None
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime

    @field_serializer("last_run_at", "next_run_at", "created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── Portal Branding ───────────────────────────────────────────────────────────

class PortalBrandingUpdate(BaseModel):
    logo_url: str | None = Field(default=None, max_length=512)
    favicon_url: str | None = Field(default=None, max_length=512)
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    company_name: str | None = Field(default=None, max_length=150)
    support_email: str | None = Field(default=None, max_length=255)
    welcome_message: str | None = None


class PortalBrandingOut(BaseModel):
    id: uuid.UUID
    logo_url: str | None
    favicon_url: str | None
    primary_color: str
    company_name: str
    support_email: str | None
    welcome_message: str | None
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}


# ── CSAT Survey ───────────────────────────────────────────────────────────────

class CsatSubmitRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


class CsatOut(BaseModel):
    ticket_id: uuid.UUID
    rating: int
    comment: str | None
    submitted_at: str  # ISO datetime string


# ── Duplicate Detection ───────────────────────────────────────────────────────

class DuplicateCheckRequest(BaseModel):
    subject: str = Field(..., min_length=3)


class DuplicateTicketOut(BaseModel):
    id: uuid.UUID
    ticket_id: str
    subject: str
    status: str
    created_at: datetime

    @field_serializer("created_at", when_used="json")
    def _ser(self, v: datetime | None) -> str | None:
        return _utc_iso(v)

    model_config = {"from_attributes": True}
