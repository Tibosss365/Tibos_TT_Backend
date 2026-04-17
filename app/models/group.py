from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Group(Base):
    """
    Ticket groups / support teams managed by admins.

    ``id`` is a URL-safe slug (e.g. 'microsoft-365', 'end-user-support')
    and doubles as the foreign key stored on Category.group_id and
    Ticket.group_id so JOIN-free look-ups remain fast.
    """

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#6B7280")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
