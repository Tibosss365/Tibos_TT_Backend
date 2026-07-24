"""Account owners — the internal staff who own a customer company.

An owner is stored as a plain dict ``{user_id, name, email}`` in the ``owners``
JSONB column on ``domain_companies`` and ``tickets``. ``user_id`` is present
when the owner was picked from the helpdesk user list, and absent when they
were entered as a free-text name + email.

Company owners are copied onto a ticket when it is created, so later edits to
the company registry never rewrite the CC list of tickets already in flight.
"""
import logging
import re
from typing import Any, Iterable

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import DomainCompany

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_owners(owners: Iterable[Any] | None) -> list[dict]:
    """Coerce owners (pydantic models or dicts) to storable dicts.

    Drops entries without a usable email and de-duplicates on the lowercased
    address, keeping the first occurrence.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for owner in owners or []:
        raw = owner if isinstance(owner, dict) else owner.model_dump()
        email = str(raw.get("email") or "").strip().lower()
        if not email or not _EMAIL_RE.match(email) or email in seen:
            continue
        seen.add(email)
        user_id = raw.get("user_id")
        out.append({
            "user_id": str(user_id) if user_id else None,
            "name": str(raw.get("name") or "").strip() or email.split("@")[0],
            "email": email,
        })
    return out


def owner_emails(owners: Iterable[Any] | None, exclude: str | None = None) -> list[str]:
    """CC-ready address list, minus ``exclude`` (normally the ticket's own To:)."""
    skip = (exclude or "").strip().lower()
    return [o["email"] for o in normalize_owners(owners) if o["email"] != skip]


def owner_label(owners: Iterable[Any] | None) -> str:
    """Human-readable 'Name <email>, Name <email>' for timeline entries."""
    return ", ".join(f"{o['name']} &lt;{o['email']}&gt;" for o in normalize_owners(owners))


async def owners_for_company(
    db: AsyncSession,
    *,
    email: str | None = None,
    company_name: str | None = None,
) -> list[dict]:
    """Owners registered for the company a ticket belongs to.

    Matched first on the requester's email domain (the authoritative key on
    ``domain_companies``), then on company name as a fallback for tickets whose
    company was typed in by hand.
    """
    try:
        if email and "@" in email:
            domain = email.split("@")[-1].lower().strip()
            res = await db.execute(
                select(DomainCompany).where(DomainCompany.domain == domain)
            )
            record = res.scalar_one_or_none()
            if record and record.owners:
                return normalize_owners(record.owners)

        if company_name and company_name.strip():
            res = await db.execute(
                select(DomainCompany).where(
                    func.lower(DomainCompany.company_name) == company_name.strip().lower()
                )
            )
            record = res.scalars().first()
            if record and record.owners:
                return normalize_owners(record.owners)
    except Exception as exc:  # registry lookup must never block ticket creation
        logger.warning(f"[owners] company owner lookup failed: {exc}")

    return []
