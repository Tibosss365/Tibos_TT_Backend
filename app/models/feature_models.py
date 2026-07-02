"""
ORM models for the feature tables added in migration 031.
  - CustomField
  - TicketTemplate
  - AutomationRule
  - WebhookConfig
  - NotificationChannel
  - Asset
  - EscalationRule
  - RecurringTicketTemplate
  - PortalBranding
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    String,
    Text,
    DateTime,
    Date,
    Integer,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CustomField(Base):
    __tablename__ = "custom_fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # text | number | date | dropdown | checkbox | url
    field_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text", server_default="text"
    )
    # For dropdown fields: list of option strings
    options: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TicketTemplate(Base):
    __tablename__ = "ticket_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    category: Mapped[str] = mapped_column(
        String(80), nullable=False, default="other", server_default="other"
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default="medium"
    )
    group_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_field_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="'{}'::jsonb"
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # ticket_created | ticket_updated | comment_added | status_changed | sla_breach
    trigger: Mapped[str] = mapped_column(
        String(50), nullable=False, default="ticket_created", server_default="ticket_created"
    )
    # [{"field": "priority", "operator": "equals", "value": "critical"}, ...]
    conditions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    # [{"type": "assign", "value": "<user_id>"}, {"type": "set_priority", "value": "high"}, ...]
    actions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    run_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class WebhookConfig(Base):
    __tablename__ = "webhook_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ["ticket_created", "ticket_updated", "ticket_resolved", ...]
    events: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # slack | teams | discord | generic
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="slack", server_default="slack"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    webhook_url: Mapped[str] = mapped_column(String(512), nullable=False)
    events: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_tag: Mapped[str | None] = mapped_column(
        String(50), nullable=True, unique=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # laptop | desktop | monitor | printer | phone | server | network | other
    type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="other", server_default="other"
    )
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    specification: Mapped[str | None] = mapped_column(Text, nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    asset_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # active | retired | in_repair | lost
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Free-text assignee (employees are usually not system users)
    assigned_to_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    assigned_to_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(150), nullable=True)
    purchase_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    warranty_expiry: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    assigned_user: Mapped["User | None"] = relationship(  # type: ignore[name-defined]
        "User", foreign_keys=[assigned_to]
    )


class AssetHistory(Base):
    """One row per assignment change on an asset (audit trail)."""
    __tablename__ = "asset_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # assigned | reassigned | unassigned
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_to_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    assigned_to_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # e.g. "Previously assigned to Ravi Kumar (ravi@tibos.in)"
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class EscalationRule(Base):
    __tablename__ = "escalation_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # critical | high | medium | low — which priority triggers this rule
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="high", server_default="high"
    )
    hours_before_escalation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4"
    )
    # list of user UUIDs (as strings) to reassign/notify
    escalate_to_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="'[]'::jsonb"
    )
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RecurringTicketTemplate(Base):
    __tablename__ = "recurring_ticket_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # cron expression, e.g. "0 9 * * 1" = every Monday 09:00
    cron_expr: Mapped[str] = mapped_column(
        String(100), nullable=False, default="0 9 * * 1", server_default="0 9 * * 1"
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        String(80), nullable=False, default="other", server_default="other"
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default="medium"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    group_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    assignee: Mapped["User | None"] = relationship(  # type: ignore[name-defined]
        "User", foreign_keys=[assignee_id]
    )


class PortalBranding(Base):
    __tablename__ = "portal_branding"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    favicon_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    primary_color: Mapped[str] = mapped_column(
        String(7), nullable=False, default="#6366f1", server_default="#6366f1"
    )
    company_name: Mapped[str] = mapped_column(
        String(150), nullable=False, default="Help Desk", server_default="Help Desk"
    )
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    welcome_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
