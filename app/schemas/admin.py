import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, ConfigDict

from app.models.admin import EmailType, OAuthProvider, SMTPSecurity


class EmailTestRequest(BaseModel):
    """
    Request body for POST /admin/email/test.
    All credentials come from the frontend form state — NOT read from DB.
    This allows testing credentials before saving.
    """
    to_email: EmailStr
    type: str  # "smtp" | "m365" | "oauth"

    # SMTP fields
    smtp_host:     Optional[str] = None
    smtp_port:     Optional[str] = "587"
    smtp_security: Optional[str] = "tls"   # "tls" | "ssl" | "none"
    smtp_from:     Optional[str] = None
    smtp_user:     Optional[str] = None
    smtp_pass:     Optional[str] = None

    # M365 / Microsoft Graph fields
    m365_tenant_id:     Optional[str] = None
    m365_client_id:     Optional[str] = None
    m365_client_secret: Optional[str] = None
    m365_from:          Optional[str] = None

    # OAuth fields
    oauth_provider:     Optional[str] = None   # "google" | "microsoft" | "custom"
    oauth_from:         Optional[str] = None
    oauth_access_token: Optional[str] = None


class SLAConfigOut(BaseModel):
    id: uuid.UUID
    critical_hours: int
    high_hours: int
    medium_hours: int
    low_hours: int
    timer_start: str
    countdown_mode: str
    work_days: list[int]
    work_start: str
    work_end: str
    pause_on: list[str]
    updated_at: datetime

    model_config = {"from_attributes": True}


class SLAConfigUpdate(BaseModel):
    critical_hours: int
    high_hours: int
    medium_hours: int
    low_hours: int
    timer_start: str = "on_creation"
    countdown_mode: str = "24_7"
    work_days: list[int] = [0, 1, 2, 3, 4]
    work_start: str = "09:00"
    work_end: str = "20:00"
    pause_on: list[str] = ["on-hold"]


class SMTPSettings(BaseModel):
    host: str = ""
    port: str = "587"
    security: SMTPSecurity = SMTPSecurity.tls
    from_address: str = ""
    user: str = ""
    password: str = ""


class M365Settings(BaseModel):
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    from_address: str = ""


class OAuthSettings(BaseModel):
    provider: OAuthProvider = OAuthProvider.google
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: str = ""
    auth_endpoint: str = ""
    token_endpoint: str = ""
    from_address: str = ""
    # Read-only token fields (populated after authorization)
    access_token: str | None = None
    refresh_token: str | None = None
    token_expiry: datetime | None = None


class EmailTriggers(BaseModel):
    trigger_new: bool = True
    trigger_assign: bool = True
    trigger_resolve: bool = True
    trigger_timezone: str = "Asia/Kolkata"


class EmailConfigOut(BaseModel):
    id: uuid.UUID
    type: EmailType
    # SMTP
    smtp_host: str | None
    smtp_port: str | None
    smtp_security: SMTPSecurity | None
    smtp_from: str | None
    smtp_user: str | None
    # M365
    m365_tenant_id: str | None
    m365_client_id: str | None
    m365_from: str | None
    # OAuth
    oauth_provider: OAuthProvider | None
    oauth_client_id: str | None
    oauth_redirect_uri: str | None
    oauth_scopes: str | None
    oauth_auth_endpoint: str | None
    oauth_token_endpoint: str | None
    oauth_from: str | None
    oauth_token_expiry: datetime | None
    # Triggers
    trigger_new: bool
    trigger_assign: bool
    trigger_resolve: bool
    trigger_timezone: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailConfigUpdate(BaseModel):
    type: EmailType
    smtp: SMTPSettings | None = None
    m365: M365Settings | None = None
    oauth: OAuthSettings | None = None
    triggers: EmailTriggers = EmailTriggers()


class OAuthCallbackRequest(BaseModel):
    """Payload sent after OAuth provider redirects back with authorization code."""
    code: str
    state: str | None = None


class OAuthAuthorizeUrl(BaseModel):
    """URL to redirect the user to for OAuth authorization."""
    url: str
    state: str


class TicketSettingsOut(BaseModel):
    number_prefix:    str
    number_digits:    int
    default_status:   str
    default_priority: str

    model_config = {"from_attributes": True}


class TicketSettingsUpdate(BaseModel):
    number_prefix:    str = "TKT"
    number_digits:    int = 4
    default_status:   str = "open"
    default_priority: str = "medium"


class AdminStats(BaseModel):
    total_tickets: int
    open_tickets: int
    in_progress_tickets: int
    resolved_tickets: int
    closed_tickets: int
    critical_tickets: int
    unassigned_tickets: int
    agent_workload: list[dict]


class AlertSettingsOut(BaseModel):
    conditions: dict
    reports: dict
    recipients: dict
    alert_email_config: dict | None = Field(default=None, alias="alertEmailConfig")
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AlertSettingsUpdate(BaseModel):
    conditions: dict
    reports: dict
    recipients: dict
    alert_email_config: dict | None = Field(default=None, alias="alertEmailConfig")


# ── Domain Company schemas ─────────────────────────────────────────────────────

class DomainCompanyOut(BaseModel):
    id: uuid.UUID
    domain: str
    company_name: str
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    logo_url: str | None = None
    auto_discovered: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DomainCompanyCreate(BaseModel):
    domain: str = Field(..., min_length=1, max_length=255,
                        description="Email domain, e.g. tibos.in")
    company_name: str = Field(..., min_length=1, max_length=255)
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=50)
    logo_url: str | None = Field(default=None, max_length=512)
    auto_discovered: bool = False


class DomainCompanyUpdate(BaseModel):
    company_name: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: str | None = Field(default=None, max_length=255)
    contact_phone: str | None = Field(default=None, max_length=50)
    logo_url: str | None = Field(default=None, max_length=512)


class DomainLookupResult(BaseModel):
    """Result from the Clearbit-based auto-discovery lookup."""
    domain: str
    company_name: str | None = None
    logo_url: str | None = None
    found: bool = False
