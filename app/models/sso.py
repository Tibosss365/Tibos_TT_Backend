import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SSOConfig(Base):
    """Stores a single OIDC / SSO provider configuration (singleton row, id=1).

    Supports two protocols controlled by ``saml_mode``:
    - False (default): OIDC / OpenID Connect flow (existing behaviour).
    - True: SAML 2.0 flow — requires idp_cert and the IdP SSO URL
      (fetched automatically from idp_metadata_url or configured via
      authorization_endpoint as the IdP SSO POST binding URL).
    """
    __tablename__ = "sso_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # ── Feature toggle ─────────────────────────────────────────────────────────
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Provider ───────────────────────────────────────────────────────────────
    # "microsoft" (Entra ID) is the only fully supported provider.
    # "custom" lets admins supply arbitrary OIDC endpoints.
    provider: Mapped[str] = mapped_column(String(50), default="microsoft", nullable=False)

    # ── SAML mode ──────────────────────────────────────────────────────────────
    # When True the /auth/saml/* endpoints are active instead of /auth/sso/*
    saml_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Azure AD / Entra ID federation metadata URL, e.g.:
    # https://login.microsoftonline.com/<tenant>/federationmetadata/2007-06/federationmetadata.xml
    # Backend auto-fetches the IdP X.509 certificate from this URL when set.
    idp_metadata_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PEM-encoded X.509 certificate from the IdP (used to verify SAML assertion
    # signatures).  Populated automatically when idp_metadata_url is saved,
    # or can be pasted manually.
    idp_cert: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Azure AD / Entra ID credentials ───────────────────────────────────────
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Backend redirect URI (must match Azure app registration exactly)
    redirect_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Custom OIDC endpoints (only used when provider = "custom") ─────────────
    authorization_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jwks_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── User provisioning ──────────────────────────────────────────────────────
    # Role assigned to newly provisioned SSO users
    default_role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)

    # Azure AD group object IDs → role mapping (stored as JSON lists)
    # e.g. admin_group_ids = ["guid-1", "guid-2"]
    admin_group_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    agent_group_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── Options ────────────────────────────────────────────────────────────────
    auto_create_users: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When True, user info (name, email) is refreshed from the ID token on every login
    sync_on_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
