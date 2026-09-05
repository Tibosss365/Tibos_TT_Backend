from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanySettings(Base):
    """Org-wide branding + general settings (singleton row, id=1).

    Previously this lived only in the browser's localStorage (Zustand
    persist) with no backend at all — every admin/device had their own
    private copy, so it silently "reset" whenever viewed from a fresh
    browser/profile. This makes it one shared, durable record instead.
    """
    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # ── Company profile (used in email templates, sidebar branding) ──────────
    name:    Mapped[str] = mapped_column(String(200), nullable=False, default="HelpdeskPro")
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone:   Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Data-URL or hosted URL — capped client-side at 2MB before upload
    logo:    Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── System settings (affects every logged-in user, not just admins) ──────
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Asia/Kolkata")
    session_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=480)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
