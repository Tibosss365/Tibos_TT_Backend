import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TeamsConfig(Base):
    """Microsoft Teams bot configuration (singleton row, id=1).

    Created via an Azure Bot resource in the Azure Portal — see the admin UI
    for the exact setup steps. app_password is the Bot's client secret,
    stored plaintext like SSOConfig.client_secret (same trust model: DB
    access already implies admin-level access to this deployment).
    """
    __tablename__ = "teams_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Azure Bot / Entra ID app credentials ──────────────────────────────────
    # Directory (tenant) ID for a Single Tenant bot, or "botframework.com" for
    # a Multi Tenant bot — whichever the Azure Bot resource was created as.
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Microsoft App ID from the Azure Bot resource (== the Entra ID app's Application ID)
    app_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Client secret created under that app registration's Certificates & secrets
    app_password: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── New-ticket defaults for tickets opened from a Teams message ──────────
    default_category: Mapped[str] = mapped_column(String(80), default="other", nullable=False)
    default_priority: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TeamsConversation(Base):
    """Links one Teams conversation (1:1 chat or channel thread) to one ticket.

    Every inbound message in that conversation is appended as a comment on
    the linked ticket; every agent comment/reply on that ticket (that opts
    to notify the customer) is relayed back into the conversation.
    """
    __tablename__ = "teams_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # Bot Framework conversation reference — required to send proactive messages later
    conversation_id: Mapped[str] = mapped_column(String(500), nullable=False, unique=True, index=True)
    service_url:     Mapped[str] = mapped_column(String(500), nullable=False)
    tenant_id:        Mapped[str | None] = mapped_column(String(255), nullable=True)

    aad_user_id:   Mapped[str | None] = mapped_column(String(255), nullable=True)
    aad_user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False,
    )
