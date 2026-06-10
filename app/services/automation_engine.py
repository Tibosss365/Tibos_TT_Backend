"""
Automation rule engine.

Evaluates active AutomationRule records against a ticket event and executes
matching actions.

Trigger names:
  ticket_created | ticket_updated | comment_added | status_changed | sla_breach

Condition schema (each item in the `conditions` JSONB list):
  {"field": "priority", "operator": "equals", "value": "critical"}
  {"field": "category", "operator": "not_equals", "value": "hardware"}
  {"field": "status", "operator": "in", "value": ["open", "in-progress"]}

Action schema (each item in the `actions` JSONB list):
  {"type": "assign",          "value": "<user_uuid>"}
  {"type": "set_priority",    "value": "high"}
  {"type": "set_status",      "value": "in-progress"}
  {"type": "add_tag",         "value": "vip"}
  {"type": "set_group",       "value": "microsoft-365"}
"""
import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")


def _get_field(ticket, field: str):
    """Extract a comparable value from the ticket ORM object."""
    mapping = {
        "priority": ticket.priority,
        "status": ticket.status,
        "category": ticket.category,
        "group_id": ticket.group_id or "",
        "source": getattr(ticket, "source", "portal"),
        "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else "",
    }
    return mapping.get(field)


def _evaluate_condition(ticket, condition: dict) -> bool:
    field = condition.get("field", "")
    operator = condition.get("operator", "equals")
    value = condition.get("value")

    ticket_val = _get_field(ticket, field)
    if ticket_val is None:
        return False

    ticket_str = str(ticket_val).lower() if ticket_val is not None else ""
    value_str = str(value).lower() if value is not None else ""

    if operator == "equals":
        return ticket_str == value_str
    if operator == "not_equals":
        return ticket_str != value_str
    if operator == "in":
        choices = [str(v).lower() for v in (value if isinstance(value, list) else [])]
        return ticket_str in choices
    if operator == "not_in":
        choices = [str(v).lower() for v in (value if isinstance(value, list) else [])]
        return ticket_str not in choices
    if operator == "contains":
        return value_str in ticket_str
    return False


def _evaluate_rule(ticket, rule) -> bool:
    """All conditions must match (AND logic)."""
    conditions = rule.conditions or []
    if not conditions:
        return True  # no conditions = always match
    return all(_evaluate_condition(ticket, c) for c in conditions)


async def _apply_action(ticket, action: dict, db: AsyncSession) -> None:
    action_type = action.get("type", "")
    value = action.get("value")

    if action_type == "assign":
        try:
            ticket.assignee_id = _uuid.UUID(str(value))
        except (ValueError, AttributeError):
            pass

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


async def run_automation(trigger: str, ticket, db: AsyncSession) -> None:
    """
    Run all active automation rules that match *trigger* and whose conditions
    match *ticket*.  Rules are evaluated in run_order ascending.
    """
    from app.models.feature_models import AutomationRule

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

    for rule in rules:
        if _evaluate_rule(ticket, rule):
            logger.debug(f"[automation] Rule '{rule.name}' matched ticket {ticket.id}")
            for action in (rule.actions or []):
                await _apply_action(ticket, action, db)
