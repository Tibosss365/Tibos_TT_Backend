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


class AlertSettings(Base):
    """Admin-configurable alert conditions, scheduled reports, and recipients."""
    __tablename__ = "alert_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # JSON blobs — structure mirrors the frontend DEFAULT_ALERT_SETTINGS shape
    conditions: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {
        "unassigned":  {"enabled": True,  "thresholdMins": 30},
        "slaBreach":   {"enabled": True,  "includeWarning": True},
        "openToday":   {"enabled": False},
        "onHold":      {"enabled": False, "thresholdHours": 24},
        "inProgress":  {"enabled": False, "thresholdHours": 48},
    })
    reports: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {
        "timezone": "Asia/Kolkata",
        "daily":    {"enabled": False, "time": "08:00"},
        "weekly":   {"enabled": False, "day": "monday",  "time": "08:00"},
        "monthly":  {"enabled": False, "dayOfMonth": 1,  "time": "08:00"},
    })
    recipients: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {
        "includeAdmin": True,
        "emails": [],
    })
    alert_email_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=lambda: {
        "useSameAsEmail": True,
        "type": "smtp",
        "smtp": {"host": "", "port": "587", "security": "tls", "from": "", "user": "", "pass": ""},
        "m365": {"tenantId": "", "clientId": "", "clientSecret": "", "from": ""},
    })
    # Tracks when each report type was last successfully sent
    # {"daily": "2026-04-21T08:00:00+00:00", "weekly": "...", "monthly": "..."}
    last_reports_sent: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

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
    trigger_timezone: Mapped[str] = mapped_column(String(50), default="Asia/Kolkata", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
