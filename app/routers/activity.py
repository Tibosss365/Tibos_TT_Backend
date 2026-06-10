"""
Activity router — provides admin-only endpoints for:
  GET /activity/logins           — paginated login sessions from DB
  GET /activity/modifications    — paginated ticket timeline entries
"""
import math
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user
from app.database import get_db
from app.models.login_session import LoginSession
from app.models.ticket import Ticket, TicketTimeline
from app.models.user import User, UserRole

router = APIRouter(prefix="/activity", tags=["activity"])


def _require_staff(current_user: User = Depends(get_current_user)) -> User:
    """Restrict to admin / technician / supervisor."""
    if current_user.role == UserRole.user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff only")
    return current_user


# ── Schemas ────────────────────────────────────────────────────────────────────

class LoginSessionOut(BaseModel):
    id: str
    user_id: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    user_role: str | None = None
    ip_address: str | None = None
    browser: str | None = None
    os: str | None = None
    logged_in_at: str
    logged_out_at: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


class PaginatedLoginSessions(BaseModel):
    items: list[LoginSessionOut]
    total: int
    page: int
    page_size: int
    pages: int


class ModificationEntryOut(BaseModel):
    id: str
    ticket_id: str           # e.g. "TKT-0042"
    ticket_uuid: str
    ticket_subject: str
    action: str              # timeline type: comment | status | assign | resolved ...
    text: str
    author_name: str | None = None
    author_id: str | None = None
    ts: str                  # ISO timestamp


class PaginatedModifications(BaseModel):
    items: list[ModificationEntryOut]
    total: int
    page: int
    page_size: int
    pages: int


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    from datetime import timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── Login History ──────────────────────────────────────────────────────────────

@router.get("/logins", response_model=PaginatedLoginSessions)
async def get_login_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None, description="Filter by username or IP"),
    role: str | None = Query(None, description="Filter by user role"),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_staff),
):
    # Base query with user join
    base = (
        select(LoginSession)
        .options(selectinload(LoginSession.user))
        .order_by(LoginSession.logged_in_at.desc())
    )

    if active_only:
        base = base.where(LoginSession.is_active == True)  # noqa: E712

    # Count
    count_stmt = select(func.count()).select_from(LoginSession)
    if active_only:
        count_stmt = count_stmt.where(LoginSession.is_active == True)  # noqa: E712

    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    # Paginate
    result = await db.execute(
        base.offset((page - 1) * page_size).limit(page_size)
    )
    sessions = result.scalars().all()

    items = []
    for sess in sessions:
        u = sess.user
        # Apply search/role filters in Python (small result set after pagination)
        if search:
            q = search.lower()
            name_match = (u.name if u else "").lower()
            email_match = (u.username if u else "").lower()
            ip_match = (sess.ip_address or "").lower()
            if q not in name_match and q not in email_match and q not in ip_match:
                continue
        if role and u and u.role.value != role:
            continue

        items.append(LoginSessionOut(
            id=str(sess.id),
            user_id=str(sess.user_id) if sess.user_id else None,
            user_name=u.name if u else None,
            user_email=u.username if u else None,
            user_role=u.role.value if u else None,
            ip_address=sess.ip_address,
            browser=sess.browser,
            os=sess.os,
            logged_in_at=_fmt_dt(sess.logged_in_at),
            logged_out_at=_fmt_dt(sess.logged_out_at),
            is_active=sess.is_active,
        ))

    return PaginatedLoginSessions(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


# ── Modification History ───────────────────────────────────────────────────────

@router.get("/modifications", response_model=PaginatedModifications)
async def get_modification_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None, description="Filter by agent name or ticket ID"),
    action: str | None = Query(None, description="Filter by timeline type (comment, status, assign...)"),
    agent_name: str | None = Query(None, description="Filter by agent display name"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_staff),
):
    # Count total timeline entries (optionally filtered)
    count_q = select(func.count()).select_from(TicketTimeline)
    if action:
        count_q = count_q.where(TicketTimeline.type == action)

    total_res = await db.execute(count_q)
    total = total_res.scalar_one()

    # Paginated timeline with ticket + author join
    stmt = (
        select(TicketTimeline)
        .options(
            selectinload(TicketTimeline.author),
            selectinload(TicketTimeline.ticket),
        )
        .order_by(TicketTimeline.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if action:
        stmt = stmt.where(TicketTimeline.type == action)

    result = await db.execute(stmt)
    entries = result.scalars().all()

    items = []
    for ev in entries:
        t = ev.ticket
        author = ev.author

        # Python-side search filter
        if search:
            q = search.lower()
            author_name_str = (author.name if author else "").lower()
            ticket_id_str = (t.ticket_id if t else "").lower()
            ticket_subject_str = (t.subject if t else "").lower()
            if q not in author_name_str and q not in ticket_id_str and q not in ticket_subject_str:
                continue
        if agent_name and (author is None or author.name != agent_name):
            continue

        import re
        clean_text = re.sub(r"<[^>]+>", "", ev.text or "")

        items.append(ModificationEntryOut(
            id=str(ev.id),
            ticket_id=t.ticket_id if t else "—",
            ticket_uuid=str(t.id) if t else "",
            ticket_subject=t.subject if t else "—",
            action=ev.type.value if hasattr(ev.type, "value") else str(ev.type),
            text=clean_text[:300],
            author_name=author.name if author else None,
            author_id=str(author.id) if author else None,
            ts=_fmt_dt(ev.created_at),
        ))

    return PaginatedModifications(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


# ── Agent leaderboard (quick summary) ─────────────────────────────────────────

class AgentSummaryOut(BaseModel):
    agent_id: str
    agent_name: str
    total: int
    comments: int
    resolved: int
    assigned: int


@router.get("/agent-summary", response_model=list[AgentSummaryOut])
async def get_agent_summary(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_staff),
):
    """Returns per-agent action counts derived from ticket timelines."""
    result = await db.execute(
        select(TicketTimeline)
        .options(selectinload(TicketTimeline.author))
        .where(TicketTimeline.author_id.isnot(None))
    )
    entries = result.scalars().all()

    summary: dict[str, AgentSummaryOut] = {}
    for ev in entries:
        author = ev.author
        if not author:
            continue
        key = str(author.id)
        if key not in summary:
            summary[key] = AgentSummaryOut(
                agent_id=key,
                agent_name=author.name,
                total=0, comments=0, resolved=0, assigned=0,
            )
        s = summary[key]
        s.total += 1
        t = ev.type.value if hasattr(ev.type, "value") else str(ev.type)
        if t == "comment":  s.comments += 1
        if t in ("resolved", "status") and "resolved" in (ev.text or "").lower():
            s.resolved += 1
        if t == "assign":   s.assigned += 1

    return sorted(summary.values(), key=lambda x: x.total, reverse=True)


# ── Admin security actions ─────────────────────────────────────────────────────

def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return current_user


@router.post("/sessions/{session_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Force-terminate an active login session immediately."""
    from datetime import datetime, timezone

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    result = await db.execute(select(LoginSession).where(LoginSession.id == sid))
    sess = result.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    sess.is_active = False
    sess.logged_out_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/users/{user_id}/force-password-reset", status_code=status.HTTP_204_NO_CONTENT)
async def force_password_reset(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(_require_admin),
):
    """Flag a user account so they must change their password on next login."""
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.must_change_password = True
    await db.commit()
