"""
Duplicate ticket detection.

Because Azure Database for PostgreSQL does not allow the pg_trgm extension,
we fall back to Python-side filtering using ILIKE on a sanitised keyword list.
The approach:
  1. Extract the top-N most significant words from the subject.
  2. Build an ILIKE filter for each keyword.
  3. Return tickets that match any keyword and were created in the last 30 days.

This is intentionally lightweight — false positives are acceptable; false
negatives (missing an obvious duplicate) are not.
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

_STOP_WORDS = {
    "a", "an", "the", "is", "in", "on", "at", "to", "for",
    "of", "and", "or", "but", "with", "from", "by", "about",
    "i", "my", "me", "we", "our", "you", "your", "it", "its",
    "not", "can", "cannot", "cant", "please", "help", "need",
    "issue", "problem", "error", "ticket",
}

_LOOKBACK_DAYS = 30
_MIN_WORD_LENGTH = 4
_MAX_KEYWORDS = 5


def _extract_keywords(subject: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]+", subject.lower())
    keywords = [
        w for w in words
        if len(w) >= _MIN_WORD_LENGTH and w not in _STOP_WORDS
    ]
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:_MAX_KEYWORDS]


async def find_duplicates(subject: str, db: AsyncSession, limit: int = 5) -> list:
    """
    Return open/in-progress tickets with similar subjects created in the last
    _LOOKBACK_DAYS days.
    """
    from app.models.ticket import Ticket, TicketStatus

    keywords = _extract_keywords(subject)
    if not keywords:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)

    conditions = [Ticket.subject.ilike(f"%{kw}%") for kw in keywords]

    stmt = (
        select(Ticket)
        .where(
            or_(*conditions),
            Ticket.is_deleted == False,
            Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]),
            Ticket.created_at >= cutoff,
        )
        .order_by(Ticket.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()
