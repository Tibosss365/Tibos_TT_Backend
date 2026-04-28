from datetime import datetime
from typing import Optional
from pydantic import BaseModel, HttpUrl


class SSOConfigUpdate(BaseModel):
    enabled: bool = False
    provider: str = "microsoft"
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None       # write-only; never echoed back
    redirect_uri: Optional[str] = None
    # Custom OIDC endpoints (ignored for microsoft provider)
    authorization_endpoint: Optional[str] = None
    token_endpoint: Optional[str] = None
    jwks_uri: Optional[str] = None
    issuer: Optional[str] = None
    # User provisioning
    default_role: str = "user"
    admin_group_ids: Optional[list[str]] = None
    agent_group_ids: Optional[list[str]] = None
    auto_create_users: bool = True
    sync_on_login: bool = True


class SSOConfigOut(BaseModel):
    """Full config returned to admin — client_secret is always masked."""
    enabled: bool
    provider: str
    tenant_id: Optional[str]
    client_id: Optional[str]
    client_secret_set: bool = False  # True if a secret is stored, but never return the raw value
    redirect_uri: Optional[str]
    authorization_endpoint: Optional[str]
    token_endpoint: Optional[str]
    jwks_uri: Optional[str]
    issuer: Optional[str]
    default_role: str
    admin_group_ids: Optional[list[str]]
    agent_group_ids: Optional[list[str]]
    auto_create_users: bool
    sync_on_login: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class SSOPublicConfig(BaseModel):
    """Minimal info the login page needs (no secrets)."""
    enabled: bool
    provider: str
    label: str   # e.g. "Sign in with Microsoft"
