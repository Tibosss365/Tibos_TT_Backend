from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.ticket import SLAStatus, Ticket, TicketPriority, TicketStatus
from app.models.user import User

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
