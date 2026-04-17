import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, DateTime, Enum as SAEnum, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EmailType(str, enum.Enum):
    smtp = "smtp"
    m365 = "m365"
    oauth = "oauth"


class OAuthProvider(str, enum.Enum):
    google = "google"
    microsoft = "microsoft"
    custom = "custom"


class SMTPSecurity(str, enum.Enum):
    tls  = "tls"
    ssl  = "ssl"
    none = "none"   # no encryption — frontend option "None / Plain"


class TicketSettings(Base):
    """Admin-configurable ticket number format and creation defaults."""
    __tablename__ = "ticket_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Ticket ID formatting
    number_prefix: Mapped[str] = mapped_column(String(20), default="TKT",    nullable=False)
    number_digits:  Mapped[int] = mapped_column(Integer,   default=4,         nullable=False)
    # Creation defaults
    default_status:   Mapped[str] = mapped_column(String(20), default="open",   nullable=False)
    default_priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SLAConfig(Base):
    __tablename__ = "sla_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Response time targets (hours per priority)
    critical_hours: Mapped[int] = mapped_column(Integer, default=1,  nullable=False)
    high_hours:     Mapped[int] = mapped_column(Integer, default=4,  nullable=False)
    medium_hours:   Mapped[int] = mapped_column(Integer, default=8,  nullable=False)
    low_hours:      Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    # Timer trigger: "on_creation" | "on_assignment"
    timer_start: Mapped[str] = mapped_column(String(20), default="on_creation", nullable=False)

    # Countdown mode: "24_7" | "business_hours"
    countdown_mode: Mapped[str] = mapped_column(String(20), default="24_7", nullable=False)

    # Business hours (only used when countdown_mode = "business_hours")
    # work_days: JSON list of ints 0–6 (0=Mon … 6=Sun), default Mon–Fri
    work_days: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4], nullable=False)
    work_start: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)  # "HH:MM"
    work_end:   Mapped[str] = mapped_column(String(5), default="20:00", nullable=False)  # "HH:MM"

    # Statuses that pause the SLA timer (JSON list of status strings)
    pause_on: Mapped[list] = mapped_column(
        JSON, default=lambda: ["on-hold"], nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class EmailConfig(Base):
    __tablename__ = "email_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[EmailType] = mapped_column(
        SAEnum(EmailType, name="emailtype"), nullable=False, default=EmailType.smtp
    )
    # SMTP fields
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[str | None] = mapped_column(String(10), nullable=True)
    smtp_security: Mapped[SMTPSecurity | None] = mapped_column(
        SAEnum(SMTPSecurity, name="smtpsecurity"), nullable=True
    )
    smtp_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_pass: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M365 fields
    m365_tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    m365_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    m365_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    m365_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # OAuth 2.0 fields
    oauth_provider: Mapped[OAuthProvider | None] = mapped_column(
        SAEnum(OAuthProvider, name="oauthprovider"), nullable=True
    )
    oauth_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oauth_client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_redirect_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_auth_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_token_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Stored tokens (encrypted in production)
    oauth_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Triggers
    trigger_new: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trigger_assign: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    trigger_resolve: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
