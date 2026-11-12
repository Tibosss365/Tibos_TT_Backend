"""
Ticket events leaving the app — outbound webhooks and Slack/Teams/Discord channels.

The dispatchers (webhook_dispatcher / notification_channels_service) deliver an
event to everything subscribed to it, but nothing used to call them, so webhooks
and notification channels created in Admin never fired. ``emit`` is the single
entry point: call it where a ticket event happens. Delivery runs in the
background on its own database session and can never break or slow the request.

Events: ticket_created | ticket_updated | ticket_resolved | ticket_deleted |
        comment_added | sla_breach
"""
import asyncio
import logging

logger = logging.getLogger("uvicorn.error")

_tasks: set[asyncio.Task] = set()   # keep references so tasks are not garbage-collected mid-flight


def _val(x):
    return getattr(x, "value", x)


def ticket_payload(ticket, **extra) -> dict:
    """A plain-dict snapshot of the ticket (taken now, so later changes don't leak in)."""
    data = {
        "id": str(ticket.id),
        "ticket_id": ticket.ticket_id,
        "subject": ticket.subject,
        "status": _val(ticket.status),
        "priority": _val(ticket.priority),
        "category": ticket.category,
        "type": ticket.type,
        "company": ticket.company,
        "requester_name": ticket.submitter_name,
        "requester_email": ticket.email,
        "source": _val(getattr(ticket, "source", None)),
        "assignee_id": str(ticket.assignee_id) if ticket.assignee_id else None,
    }
    data.update(extra)
    return data


async def _deliver(event: str, payload: dict) -> None:
    try:
        from app.database import AsyncSessionLocal
        from app.services.webhook_dispatcher import dispatch_event
        from app.services.notification_channels_service import notify_event

        async with AsyncSessionLocal() as db:
            await dispatch_event(event, payload, db)
        async with AsyncSessionLocal() as db:
            await notify_event(event, payload, db)
    except Exception as exc:  # never let delivery problems surface anywhere
        logger.warning(f"[events] delivery of '{event}' failed: {exc}")


def emit(event: str, ticket, **extra) -> None:
    """Fire-and-forget. Safe to call from any request or background task."""
    try:
        payload = ticket_payload(ticket, **extra)
        task = asyncio.get_running_loop().create_task(_deliver(event, payload), name=f"event-{event}")
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as exc:
        logger.warning(f"[events] could not queue '{event}': {exc}")
