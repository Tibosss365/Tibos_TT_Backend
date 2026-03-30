"""
Creates notifications in DB and pushes them via SSE + WebSocket.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationType
from app.models.ticket import Ticket
from app.models.user import User
from app.services.sse_manager import SSEEvent, sse_manager
from app.services.ws_manager import ws_manager


async def _push_live(user_id: str, notif: Notification) -> None:
    payload = {
        "id": str(notif.id),
        "text": notif.text,
        "type": notif.type.value,
        "read": notif.read,
        "ticket_id": str(notif.ticket_id) if notif.ticket_id else None,
        "created_at": notif.created_at.isoformat(),
    }
    sse_event = SSEEvent(event="notification", data=payload)
    await sse_manager.broadcast_to_user(user_id, sse_event)
    await ws_manager.send_to_user(user_id, {"type": "notification", **payload})


async def create_notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    text: str,
    notif_type: NotificationType,
    ticket_id: uuid.UUID | None = None,
    push_live: bool = True,
) -> Notification:
    notif = Notification(
        user_id=user_id,
        ticket_id=ticket_id,
        text=text,
        type=notif_type,
        read=False,
    )
    db.add(notif)
    await db.flush()  # get the id before commit
    await db.refresh(notif)

    if push_live:
        await _push_live(str(user_id), notif)
    return notif


async def notify_ticket_created(
    db: AsyncSession,
    ticket: Ticket,
    all_admin_users: list[User],
) -> None:
    """Notify all admins when a new ticket is created."""
    msg = f"{ticket.ticket_id} — New ticket: {ticket.subject}"
    notif_type = (
        NotificationType.critical
        if ticket.priority.value == "critical"
        else NotificationType.info
    )
    for admin in all_admin_users:
        await create_notification(db, admin.id, msg, notif_type, ticket.id)


async def notify_ticket_assigned(
    db: AsyncSession,
    ticket: Ticket,
    assignee: User,
    actor_name: str,
) -> None:
    msg = f"{ticket.ticket_id} — Assigned to you by {actor_name}"
    await create_notification(
        db, assignee.id, msg, NotificationType.info, ticket.id
    )


async def notify_ticket_resolved(
    db: AsyncSession,
    ticket: Ticket,
    resolved_by: str,
    submitter_user: User | None,
    all_admin_users: list[User],
) -> None:
    msg = f"{ticket.ticket_id} — Resolved by {resolved_by}"
    recipients: list[User] = list(all_admin_users)
    if submitter_user and submitter_user not in recipients:
        recipients.append(submitter_user)
    for user in recipients:
        await create_notification(db, user.id, msg, NotificationType.success, ticket.id)


async def broadcast_ticket_event(
    event_name: str,
    ticket_data: dict,
    actor_user_id: str | None = None,
) -> None:
    """Broadcast a ticket change to all SSE/WS subscribers (except actor)."""
    sse_event = SSEEvent(event=event_name, data=ticket_data)
    await sse_manager.broadcast_to_all(sse_event)
    await ws_manager.broadcast(
        {"type": event_name, **ticket_data},
        exclude_user=actor_user_id,
    )
