from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.ticket import SLAStatus, Ticket, TicketPriority, TicketStatus
from app.models.user import User
from app.redis_client import get_redis
from app.services import cache_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _base_filters(
    stmt,
    date_from: datetime | None,
    date_to: datetime | None,
    priority: TicketPriority | None,
    category: str | None,
    status: TicketStatus | None,
):
    if date_from:
        stmt = stmt.where(Ticket.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Ticket.created_at <= date_to)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if category:
        stmt = stmt.where(Ticket.category == category)
    if status:
        stmt = stmt.where(Ticket.status == status)
    return stmt


@router.get("/stats")
async def dashboard_stats(
    date_from: datetime | None = Query(None, description="Created-at lower bound (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Created-at upper bound (ISO 8601)"),
    priority: TicketPriority | None = Query(None),
    category: str | None = Query(None),
    status: TicketStatus | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Aggregated dashboard stats with optional filters.
    Returns status counts, SLA overdue count, and distributions
    that reflect only the filtered subset of tickets.
    """

    def f(stmt):
        return _base_filters(stmt, date_from, date_to, priority, category, status)

    # ── Status counts ────────────────────────────────────────────────────────
    status_counts: dict[str, int] = {}
    for s in TicketStatus:
        res = await db.execute(
            f(select(func.count()).select_from(Ticket).where(Ticket.status == s))
        )
        status_counts[s.value] = res.scalar_one()

    # ── Totals ───────────────────────────────────────────────────────────────
    total_res = await db.execute(f(select(func.count()).select_from(Ticket)))
    total = total_res.scalar_one()

    critical_res = await db.execute(
        f(select(func.count()).select_from(Ticket).where(Ticket.priority == TicketPriority.critical))
    )
    critical = critical_res.scalar_one()

    # ── SLA overdue ──────────────────────────────────────────────────────────
    sla_overdue_res = await db.execute(
        f(
            select(func.count()).select_from(Ticket).where(
                or_(
                    Ticket.sla_status == SLAStatus.overdue,
                    (Ticket.sla_status == SLAStatus.active)
                    & (Ticket.sla_due_time < func.now()),
                )
            )
        )
    )
    sla_overdue = sla_overdue_res.scalar_one()

    # ── Category distribution ─────────────────────────────────────────────────
    cat_rows = await db.execute(
        f(
            select(Ticket.category, func.count().label("count")).group_by(Ticket.category)
        )
    )
    category_dist: dict[str, int] = {row[0]: row[1] for row in cat_rows.all()}

    # ── Priority distribution ─────────────────────────────────────────────────
    pri_dist: dict[str, int] = {}
    for p in TicketPriority:
        res = await db.execute(
            f(select(func.count()).select_from(Ticket).where(Ticket.priority == p))
        )
        pri_dist[p.value] = res.scalar_one()

    return {
        "total": total,
        "open": status_counts.get("open", 0),
        "in_progress": status_counts.get("in-progress", 0),
        "on_hold": status_counts.get("on-hold", 0),
        "resolved": status_counts.get("resolved", 0),
        "closed": status_counts.get("closed", 0),
        "critical": critical,
        "sla_overdue": sla_overdue,
        "status_counts": status_counts,
        "category_distribution": category_dist,
        "priority_distribution": pri_dist,
    }


@router.get("/agent-stats")
async def agent_stats(
    date_from: datetime | None = Query(None, description="Created-at lower bound (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Created-at upper bound (ISO 8601)"),
    priority: TicketPriority | None = Query(None),
    category: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Per-agent ticket counts broken down by status.
    Used by the 'Agent Wise Ticket Count' table on the dashboard.

    Returns all active agents (even those with 0 tickets) with counts for:
      created   – total tickets assigned to this agent (matching filters)
      open      – status = open
      in_progress – status = in-progress
      on_hold   – status = on-hold
      resolved  – status = resolved OR closed
    """
    cache_params = {
        "date_from": str(date_from) if date_from else None,
        "date_to": str(date_to) if date_to else None,
        "priority": priority.value if priority else None,
        "category": category,
    }
    redis = await get_redis()
    cache_key = cache_service.agent_stats_key(cache_params)
    cached = await cache_service.cache_get(redis, cache_key)
    if cached:
        return cached

    # Build the join condition dynamically so ticket-side filters are applied
    # in the ON clause — this preserves agents with no matching tickets (count = 0).
    join_cond = Ticket.assignee_id == User.id
    if date_from:
        join_cond = and_(join_cond, Ticket.created_at >= date_from)
    if date_to:
        join_cond = and_(join_cond, Ticket.created_at <= date_to)
    if priority:
        join_cond = and_(join_cond, Ticket.priority == priority)
    if category:
        join_cond = and_(join_cond, Ticket.category == category)

    stmt = (
        select(
            User.id,
            User.name,
            User.initials,
            func.count(Ticket.id).label("created"),
            func.sum(
                case((Ticket.status == TicketStatus.open, 1), else_=0)
            ).label("open"),
            func.sum(
                case((Ticket.status == TicketStatus.in_progress, 1), else_=0)
            ).label("in_progress"),
            func.sum(
                case((Ticket.status == TicketStatus.on_hold, 1), else_=0)
            ).label("on_hold"),
            func.sum(
                case(
                    (Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]), 1),
                    else_=0,
                )
            ).label("resolved"),
        )
        .select_from(User)
        .outerjoin(Ticket, join_cond)
        .where(User.is_active == True)  # noqa: E712
        .group_by(User.id, User.name, User.initials)
        .order_by(User.name)
    )

    rows = (await db.execute(stmt)).all()
    result = [
        {
            "agent_id": str(row.id),
            "name": row.name,
            "initials": row.initials,
            "created": row.created,
            "open": row.open or 0,
            "in_progress": row.in_progress or 0,
            "on_hold": row.on_hold or 0,
            "resolved": row.resolved or 0,
        }
        for row in rows
    ]

    await cache_service.cache_set(redis, cache_key, result, cache_service.STATS_TTL)
    return result
