import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SSOConfig(Base):
    """Stores a single OIDC / SSO provider configuration (singleton row, id=1)."""
    __tablename__ = "sso_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # ── Feature toggle ─────────────────────────────────────────────────────────
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Provider ───────────────────────────────────────────────────────────────
    # "microsoft" (Entra ID) is the only fully supported provider.
    # "custom" lets admins supply arbitrary OIDC endpoints.
    provider: Mapped[str] = mapped_column(String(50), default="microsoft", nullable=False)

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
