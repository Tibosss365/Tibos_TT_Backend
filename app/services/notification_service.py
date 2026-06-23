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
        "is_approval": notif.is_approval,
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
    is_approval: bool = False,
) -> Notification:
    notif = Notification(
        user_id=user_id,
        ticket_id=ticket_id,
        text=text,
        type=notif_type,
        read=False,
        is_approval=is_approval,
    )
    db.add(notif)
    await db.flush()  # get the id before commit
    await db.refresh(notif)

    if push_live:
        await _push_live(str(user_id), notif)
    return notif


async def notify_approval_requested(
    db: AsyncSession,
    ticket: Ticket,
    approver: User,
    requested_by: str,
    note: str | None = None,
) -> None:
    """When an approval is raised on a ticket, notify + email the approver.

    The notification is flagged is_approval=True so it pins to the top of the
    list and is NOT removed by the "Clear notifications" action.
    The email is best-effort — failure to send must not break the API call.
    """
    note_suffix = f": {note}" if note else ""
    msg = f"{ticket.ticket_id} — Approval requested by {requested_by}{note_suffix}"
    await create_notification(
        db,
        approver.id,
        msg,
        NotificationType.warning,
        ticket.id,
        is_approval=True,
    )

    # Best-effort email to the approver (only if their login looks like an email).
    if "@" not in (approver.username or ""):
        return
    try:
        from app.services.email_sender import send_ticket_email

        note_html = (
            f'<p style="margin:12px 0;color:#475569;">{note}</p>' if note else ""
        )
        body_html = (
            f"<p>Hi {approver.name},</p>"
            f"<p><strong>{requested_by}</strong> has requested your approval on ticket "
            f"<strong>{ticket.ticket_id}</strong> — {ticket.subject}.</p>"
            f"{note_html}"
            f"<p>Please open the ticket to approve or reject this request.</p>"
        )
        await send_ticket_email(
            db,
            ticket,
            to_email=approver.username,
            subject=f"Approval requested: {ticket.ticket_id} — {ticket.subject}",
            body_html=body_html,
            action_label="Review Approval",
        )
    except Exception:  # noqa: BLE001 — email is best-effort
        pass


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
