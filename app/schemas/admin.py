import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.admin import EmailType, OAuthProvider, SMTPSecurity


class SLAConfigOut(BaseModel):
    id: uuid.UUID
    critical_hours: int
    high_hours: int
    medium_hours: int
    low_hours: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class SLAConfigUpdate(BaseModel):
    critical_hours: int
    high_hours: int
    medium_hours: int
    low_hours: int


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


class AdminStats(BaseModel):
    total_tickets: int
    open_tickets: int
    in_progress_tickets: int
    resolved_tickets: int
    closed_tickets: int
    critical_tickets: int
    unassigned_tickets: int
    agent_workload: list[dict]
