from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.admin import SLAConfig
from app.models.ticket import Ticket, TicketPriority, TicketStatus
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.analytics import AnalyticsOut
from app.services import cache_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("", response_model=AnalyticsOut)
async def get_analytics(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    redis = await get_redis()
    cache_key = cache_service.analytics_key()
    cached = await cache_service.cache_get(redis, cache_key)
    if cached:
        return cached

    # Status distribution
    status_dist: dict[str, int] = {}
    for s in TicketStatus:
        res = await db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.status == s)
        )
        status_dist[s.value] = res.scalar_one()

    # Category distribution — group by actual stored string values (supports custom categories)
    cat_rows = await db.execute(
        select(Ticket.category, func.count()).group_by(Ticket.category)
    )
    cat_dist: dict[str, int] = {row[0]: row[1] for row in cat_rows.all()}

    # Priority distribution
    pri_dist: dict[str, int] = {}
    for p in TicketPriority:
        res = await db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.priority == p)
        )
        pri_dist[p.value] = res.scalar_one()

    # Resolution rate
    total_res = await db.execute(select(func.count()).select_from(Ticket))
    total = total_res.scalar_one()
    resolved_count = status_dist.get("resolved", 0) + status_dist.get("closed", 0)
    resolution_rate = (resolved_count / total * 100) if total else 0.0

    # SLA compliance (% of resolved tickets resolved within SLA time)
    sla_res = await db.execute(select(SLAConfig))
    sla = sla_res.scalar_one_or_none()
    sla_hours = {
        "critical": sla.critical_hours if sla else 1,
        "high": sla.high_hours if sla else 4,
        "medium": sla.medium_hours if sla else 8,
        "low": sla.low_hours if sla else 24,
    }

    sla_compliance: dict[str, float] = {}
    for p in TicketPriority:
        hours = sla_hours.get(p.value, 8)
        total_p_res = await db.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.priority == p,
                Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]),
            )
        )
        total_p = total_p_res.scalar_one()
        if total_p == 0:
            sla_compliance[p.value] = 100.0
            continue

        within_sla_res = await db.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.priority == p,
                Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]),
                (Ticket.updated_at - Ticket.created_at) <= timedelta(hours=hours),
            )
        )
        within = within_sla_res.scalar_one()
        sla_compliance[p.value] = round(within / total_p * 100, 1)

    # Tickets over time (last 30 days, grouped by day)
    # Use literal_column("'day'") so the unit is embedded as a SQL literal,
    # not a bind parameter — otherwise PostgreSQL rejects the GROUP BY because
    # it sees different parameter slots ($1 vs $3) as different expressions.
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    day_trunc = func.date_trunc(literal_column("'day'"), Ticket.created_at).label("day")
    daily_res = await db.execute(
        select(day_trunc, func.count().label("count"))
        .where(Ticket.created_at >= thirty_days_ago)
        .group_by(day_trunc)
        .order_by(day_trunc)
    )
    tickets_over_time = [
        {"date": row.day.strftime("%Y-%m-%d"), "count": row.count}
        for row in daily_res.all()
    ]

    # Average resolution time (hours)
    avg_res = await db.execute(
        select(
            func.avg(
                func.extract("epoch", Ticket.updated_at - Ticket.created_at) / 3600
            )
        )
        .where(Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]))
    )
    avg_hours_raw = avg_res.scalar_one()
    avg_resolution_hours = round(float(avg_hours_raw), 1) if avg_hours_raw else 0.0

    analytics = AnalyticsOut(
        status_distribution=status_dist,
        category_distribution=cat_dist,
        priority_distribution=pri_dist,
        resolution_rate=round(resolution_rate, 1),
        sla_compliance=sla_compliance,
        tickets_over_time=tickets_over_time,
        avg_resolution_hours=avg_resolution_hours,
    )

    await cache_service.cache_set(
        redis, cache_key, analytics.model_dump(), cache_service.ANALYTICS_TTL
    )
    return analytics
