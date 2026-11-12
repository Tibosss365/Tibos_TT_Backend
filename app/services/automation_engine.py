"""
Automation rule engine.

Evaluates active AutomationRule records against a ticket event and executes
matching actions.

Trigger names:
  ticket_created | ticket_updated | comment_added | status_changed | sla_breach

Condition schema (each item in the `conditions` JSONB list; all must match):
  {"field": "priority", "operator": "equals", "value": "critical"}
  {"field": "subject",  "operator": "contains", "value": "password"}
  {"field": "status",   "operator": "in", "value": ["open", "in-progress"]}

  Fields:    priority, status, category, type, group_id, source, assignee_id,
             subject, description, company, email, email_domain
  Operators: equals, not_equals, in, not_in, contains, not_contains, starts_with

Action schema (each item in the `actions` JSONB list):
  {"type": "assign",             "value": "<user_uuid>"}
  {"type": "assign_least_busy",  "value": "<group_id>"}   # or "*" for any active agent
  {"type": "set_priority",       "value": "high"}
  {"type": "set_status",         "value": "in-progress"}
  {"type": "set_category",       "value": "<category slug>"}
  {"type": "set_type",           "value": "incident"}
  {"type": "add_tag",            "value": "vip"}
  {"type": "set_group",          "value": "microsoft-365"}

assign_least_busy load-balances: among active technicians/admins in the
given group (or every active agent when value is "*"), it picks whoever
currently has the fewest open/in-progress/on-hold tickets assigned. Ties
are broken by name so the pick is deterministic. If no eligible agent is
found, the ticket is left as-is rather than raising.

Whenever a rule actually changes the ticket, a short "Automation" note is added
to its history, and a newly assigned agent gets the same treatment as a manual
assignment (moves to In Progress, SLA timer starts, notification).
"""
import logging
import uuid as _uuid
from datetime import datetime, timezone
from html import escape as _esc

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")

_DESCRIPTION_CHARS = 5000   # enough for keyword rules without reading huge email bodies


def _val(x):
    """Plain value of an enum member (``str(Enum)`` is 'Class.member', never the value)."""
    return getattr(x, "value", x)


def _get_field(ticket, field: str):
    """Extract a comparable value from the ticket ORM object."""
    email = (ticket.email or "")
    mapping = {
        "priority": _val(ticket.priority),
        "status": _val(ticket.status),
        "category": ticket.category,
        "type": ticket.type or "request",
        "group_id": ticket.group_id or "",
        "source": _val(getattr(ticket, "source", "portal")),
        "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else "",
        "subject": ticket.subject or "",
        "description": (ticket.description or "")[:_DESCRIPTION_CHARS],
        "company": ticket.company or "",
        "email": email,
        "email_domain": email.split("@", 1)[1] if "@" in email else "",
    }
    return mapping.get(field)


def _evaluate_condition(ticket, condition: dict) -> bool:
    field = condition.get("field", "")
    operator = condition.get("operator", "equals")
    value = condition.get("value")

    ticket_val = _get_field(ticket, field)
    if ticket_val is None:
        return False

    ticket_str = str(ticket_val).lower()
    value_str = str(value).lower() if value is not None else ""

    if operator == "equals":
        return ticket_str == value_str
    if operator == "not_equals":
        return ticket_str != value_str
    if operator in ("in", "not_in"):
        raw = value if isinstance(value, list) else str(value or "").split(",")
        choices = [str(v).strip().lower() for v in raw if str(v).strip()]
        return (ticket_str in choices) if operator == "in" else (ticket_str not in choices)
    if operator == "contains":
        return value_str in ticket_str
    if operator == "not_contains":
        return value_str not in ticket_str
    if operator == "starts_with":
        return ticket_str.startswith(value_str)
    return False


def _evaluate_rule(ticket, rule) -> bool:
    """All conditions must match (AND logic)."""
    conditions = rule.conditions or []
    if not conditions:
        return True  # no conditions = always match
    return all(_evaluate_condition(ticket, c) for c in conditions)


def _snapshot(ticket) -> dict:
    return {
        "assignee": ticket.assignee_id, "priority": _val(ticket.priority), "status": _val(ticket.status),
        "category": ticket.category, "type": ticket.type, "group": ticket.group_id,
        "tags": tuple(ticket.tags or []),
    }


async def _apply_action(ticket, action: dict, db: AsyncSession) -> None:
    action_type = action.get("type", "")
    value = action.get("value")

    if action_type == "assign":
        try:
            ticket.assignee_id = _uuid.UUID(str(value))
        except (ValueError, AttributeError):
            pass

    elif action_type == "assign_least_busy":
        from app.models.user import User, UserRole
        from app.models.ticket import Ticket, TicketStatus
        from sqlalchemy import func

        group_filter = str(value).strip() if value else ""
        agents_stmt = select(User).where(
            User.is_active == True,
            User.role.in_([UserRole.admin, UserRole.technician]),
        )
        if group_filter and group_filter != "*":
            agents_stmt = agents_stmt.where(User.group == group_filter)
        agents_stmt = agents_stmt.order_by(User.name)
        agents = (await db.execute(agents_stmt)).scalars().all()
        if not agents:
            logger.warning(f"[automation] assign_least_busy: no active agent found for group '{group_filter or '*'}'")
        else:
            open_statuses = (TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold)
            counts_stmt = (
                select(Ticket.assignee_id, func.count(Ticket.id))
                .where(Ticket.assignee_id.in_([a.id for a in agents]), Ticket.status.in_(open_statuses))
                .group_by(Ticket.assignee_id)
            )
            load = dict((await db.execute(counts_stmt)).all())
            winner = min(agents, key=lambda a: load.get(a.id, 0))
            ticket.assignee_id = winner.id

    elif action_type == "set_priority":
        from app.models.ticket import TicketPriority
        try:
            ticket.priority = TicketPriority(value)
        except ValueError:
            pass

    elif action_type == "set_status":
        from app.models.ticket import TicketStatus
        try:
            ticket.status = TicketStatus(value)
        except ValueError:
            pass

    elif action_type == "add_tag":
        tags = list(ticket.tags or [])
        if value and str(value) not in tags:
            tags.append(str(value))
            ticket.tags = tags

    elif action_type == "set_group":
        ticket.group_id = str(value) if value else None

    elif action_type == "set_category":
        if value:
            ticket.category = str(value)

    elif action_type == "set_type":
        if value and str(value) in ("request", "incident", "problem", "change"):
            ticket.type = str(value)


def _describe_changes(before: dict, after: dict) -> str:
    parts = []
    if after["assignee"] != before["assignee"]:
        parts.append("assigned" if after["assignee"] else "unassigned")
    for key, label in (("priority", "priority"), ("status", "status"), ("category", "category"),
                       ("type", "type"), ("group", "group")):
        if after[key] != before[key]:
            parts.append(f"{label} → {after[key] or '—'}")
    new_tags = [t for t in after["tags"] if t not in before["tags"]]
    if new_tags:
        parts.append("tag " + ", ".join(new_tags))
    return "; ".join(parts)


async def _after_assignment(ticket, db: AsyncSession) -> None:
    """Treat an automatic assignment like a manual one: In Progress, SLA starts, agent is told."""
    from app.models.ticket import SLAStatus, TicketStatus
    from app.models.user import User

    if _val(ticket.status) == TicketStatus.open.value:
        ticket.status = TicketStatus.in_progress
    if ticket.sla_status == SLAStatus.not_started:
        from app.services.sla_service import SLAService
        now = datetime.now(timezone.utc)
        await SLAService.start(ticket, db, is_assignment=True, start_time=now)
    agent = (await db.execute(select(User).where(User.id == ticket.assignee_id))).scalar_one_or_none()
    if agent:
        from app.services.notification_service import notify_ticket_assigned
        await notify_ticket_assigned(db, ticket, agent, "Automation")


async def run_automation(trigger: str, ticket, db: AsyncSession) -> list[str]:
    """
    Run all active automation rules that match *trigger* and whose conditions
    match *ticket*.  Rules are evaluated in run_order ascending.
    Returns the names of the rules that changed the ticket.
    """
    from app.models.feature_models import AutomationRule
    from app.models.ticket import TicketTimeline, TimelineType

    stmt = (
        select(AutomationRule)
        .where(
            AutomationRule.is_active == True,
            AutomationRule.trigger == trigger,
        )
        .order_by(AutomationRule.run_order.asc())
    )
    result = await db.execute(stmt)
    rules = result.scalars().all()

    changed_by: list[str] = []
    for rule in rules:
        # One misconfigured rule must not stop the others (or the ticket flow that called us).
        # No savepoint here on purpose: rolling one back would expire the ticket object
        # the caller is still using.
        try:
            if not _evaluate_rule(ticket, rule):
                continue
            logger.debug(f"[automation] Rule '{rule.name}' matched ticket {ticket.id}")
            before = _snapshot(ticket)
            for action in (rule.actions or []):
                await _apply_action(ticket, action, db)
            after = _snapshot(ticket)
            summary = _describe_changes(before, after)
            if not summary:
                continue            # nothing actually changed (e.g. tag already present) — stay quiet
            changed_by.append(rule.name)
            if before["assignee"] is None and after["assignee"] is not None:
                await _after_assignment(ticket, db)
            db.add(TicketTimeline(
                ticket_id=ticket.id,
                type=TimelineType.comment,
                text=f"⚙ Automation rule <strong>{_esc(rule.name)}</strong> ran on <em>{trigger.replace('_', ' ')}</em>: {_esc(summary)}",
                author_id=None,
            ))
        except Exception as exc:
            logger.warning(f"[automation] rule '{getattr(rule, 'name', '?')}' failed on {trigger}: {exc}")
    return changed_by
