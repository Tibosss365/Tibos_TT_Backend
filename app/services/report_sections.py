"""Data gathering and HTML rendering for the optional report sections."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from html import escape, unescape

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole


COMPLETED = (TicketStatus.resolved, TicketStatus.closed)


def report_subject(template: dict | None, now: datetime, report_type: str = "daily") -> str:
    defaults = {
        "daily": "📊 Daily Helpdesk Report — {date}",
        "weekly": "📆 Weekly Helpdesk Report — Week of {date}",
        "monthly": "🗓️ Monthly Helpdesk Report — {month} {year}",
    }
    raw = (template or {}).get("subject") or defaults.get(report_type, "Helpdesk Report — {date}")
    day = f"{now.day} {now.strftime('%b')} {now.year}"
    return unescape(str(raw)).replace("{date}", day).replace(
        "{month}", now.strftime("%B")
    ).replace("{year}", str(now.year)).replace("{system_name}", "Tibos Helpdesk")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def report_time_bounds(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Return UTC start of local day, next local day, and local month."""
    day_start = _utc(datetime.combine(now.date(), time.min, tzinfo=now.tzinfo))
    tomorrow_start = _utc(datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo))
    month_start = _utc(datetime.combine(now.date().replace(day=1), time.min, tzinfo=now.tzinfo))
    return day_start, tomorrow_start, month_start


def _selected_ids(template: dict | None) -> set[str] | None:
    """Read optional agent selections saved with the report/dashboard template."""
    template = template or {}
    raw = None
    for key in ("selectedAgentIds", "agentIds", "selectedAgents", "agent_ids"):
        if key in template:
            raw = template[key]
            break
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, set)):
        return None
    return {str(item.get("id") if isinstance(item, dict) else item) for item in raw if item is not None}


async def gather_report_sections(
    db: AsyncSession,
    now: datetime,
    template: dict | None = None,
) -> dict[str, list[dict]]:
    """Gather the three optional report datasets using the report timezone."""
    selected = _selected_ids(template)
    users_result = await db.execute(
        select(User).where(
            User.is_active.is_(True),
            User.role.in_([UserRole.technician, UserRole.admin]),
        ).order_by(User.name)
    )
    users = users_result.scalars().all()
    if selected is not None:
        users = [user for user in users if str(user.id) in selected]
    user_ids = [user.id for user in users]
    names = {user.id: (user.name or user.username) for user in users}

    empty = {"agent_performance": [], "daily_agent_report": [], "category_specialists": []}
    if not user_ids:
        return empty

    now_utc = _utc(now)
    day_start, tomorrow_start, month_start = report_time_bounds(now)
    hour_1 = now_utc - timedelta(hours=1)
    hour_2 = now_utc - timedelta(hours=2)
    hour_24 = now_utc - timedelta(hours=24)

    perf_result = await db.execute(
        select(Ticket.assignee_id, func.count(Ticket.id))
        .where(Ticket.assignee_id.in_(user_ids), Ticket.status.in_(COMPLETED))
        .group_by(Ticket.assignee_id)
    )
    perf_counts = {row[0]: int(row[1] or 0) for row in perf_result.all()}
    performance = [{"name": names[user.id], "count": perf_counts.get(user.id, 0)} for user in users]

    created_today = func.sum(case(((Ticket.created_at >= day_start) & (Ticket.created_at < tomorrow_start), 1), else_=0))
    unresolved = Ticket.status.notin_(COMPLETED)
    daily_result = await db.execute(
        select(
            Ticket.assignee_id,
            created_today.label("created_today"),
            func.sum(case((unresolved & (Ticket.created_at > hour_1) & (Ticket.created_at <= now_utc), 1), else_=0)).label("under_1h"),
            func.sum(case((unresolved & (Ticket.created_at > hour_2) & (Ticket.created_at <= hour_1), 1), else_=0)).label("one_to_two_hours"),
            func.sum(case((unresolved & (Ticket.created_at >= hour_24) & (Ticket.created_at <= hour_2), 1), else_=0)).label("two_to_24_hours"),
            func.sum(case((Ticket.status.in_(COMPLETED) & (Ticket.updated_at >= day_start) & (Ticket.updated_at < tomorrow_start), 1), else_=0)).label("resolved_today"),
            func.sum(case((Ticket.created_at >= month_start, 1), else_=0)).label("created_this_month"),
        )
        .where(Ticket.assignee_id.in_(user_ids))
        .group_by(Ticket.assignee_id)
    )
    daily_counts = {row[0]: row._mapping for row in daily_result.all()}
    daily = []
    for user in users:
        row = daily_counts.get(user.id, {})
        daily.append({
            "name": names[user.id],
            "created_today": int(row.get("created_today") or 0),
            "under_1h": int(row.get("under_1h") or 0),
            "one_to_two_hours": int(row.get("one_to_two_hours") or 0),
            "two_to_24_hours": int(row.get("two_to_24_hours") or 0),
            "resolved_today": int(row.get("resolved_today") or 0),
            "created_this_month": int(row.get("created_this_month") or 0),
        })

    specialist_result = await db.execute(
        select(Ticket.category, Ticket.assignee_id, func.count(Ticket.id))
        .where(
            Ticket.assignee_id.in_(user_ids),
            Ticket.status.in_(COMPLETED),
        )
        .group_by(Ticket.category, Ticket.assignee_id)
        .order_by(Ticket.category, Ticket.assignee_id)
    )
    category_result = await db.execute(select(Category.slug, Category.name))
    category_names = {slug: name for slug, name in category_result.all()}
    specialists = [
        {
            "category": category_names.get(category, category or "Uncategorized"),
            "agent": names[agent_id],
            "count": int(count or 0),
        }
        for category, agent_id, count in specialist_result.all()
        if agent_id in names
    ]
    return {
        "agent_performance": performance,
        "daily_agent_report": daily,
        "category_specialists": specialists,
    }


def render_report_sections(data: dict[str, list[dict]], template: dict | None) -> str:
    """Render enabled sections as Outlook-friendly, table-based HTML."""
    template = template or {}
    sections: list[str] = []

    def table(title: str, headers: list[str], rows: list[list[object]]) -> None:
        if not rows:
            rows = [["No data", *([""] * (len(headers) - 1))]]
        head = "".join(
            f'<th align="left" style="padding:9px 10px;background:#f3f4f6;border:1px solid #e5e7eb;'
            f'font-size:11px;color:#374151;">{escape(str(label))}</th>'
            for label in headers
        )
        body = "".join(
            "<tr>" + "".join(
                f'<td style="padding:8px 10px;border:1px solid #e5e7eb;font-size:12px;color:#374151;">'
                f'{escape(str(value))}</td>' for value in row
            ) + "</tr>"
            for row in rows
        )
        sections.append(
            '<tr><td style="padding:20px 24px 8px;">'
            f'<div style="font-size:15px;font-weight:700;color:#374151;padding-bottom:10px;">{escape(title)}</div>'
            '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
            f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></td></tr>'
        )

    if template.get("includeAgentPerformance") is True:
        table("Agent Performance", ["Agent", "Resolved / Closed"], [
            [row["name"], row["count"]] for row in data.get("agent_performance", [])
        ])
    if template.get("includeDailyAgentReport") is True:
        table("Daily Report", ["Agent", "Created Today", "Under 1 Hour", "1–2 Hours", "2–24 Hours", "Resolved Today", "Created This Month"], [
            [row["name"], row["created_today"], row["under_1h"], row["one_to_two_hours"], row["two_to_24_hours"], row["resolved_today"], row["created_this_month"]]
            for row in data.get("daily_agent_report", [])
        ])
    if template.get("includeCategorySpecialists") is True:
        table("Category Specialists · Completed Tickets", ["Category", "Agent", "Completed"], [
            [row["category"], row["agent"], row["count"]]
            for row in data.get("category_specialists", [])
        ])
    return "".join(sections)
