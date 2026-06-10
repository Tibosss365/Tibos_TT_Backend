"""
Notification channels dispatcher — Slack / Teams / Discord / Generic.

Sends a formatted message to all active notification channel records that
subscribe to the given event name.
"""
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")

_TIMEOUT = 8.0


def _build_slack_payload(event: str, data: dict) -> dict:
    ticket_id = data.get("ticket_id", "")
    subject = data.get("subject", "")
    status = data.get("status", "")
    priority = data.get("priority", "")
    return {
        "text": f"[{event.replace('_', ' ').title()}] *{ticket_id}* — {subject}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Event:* {event}\n"
                        f"*Ticket:* {ticket_id}\n"
                        f"*Subject:* {subject}\n"
                        f"*Status:* {status}  *Priority:* {priority}"
                    ),
                },
            }
        ],
    }


def _build_teams_payload(event: str, data: dict) -> dict:
    ticket_id = data.get("ticket_id", "")
    subject = data.get("subject", "")
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": f"{event} — {ticket_id}",
        "themeColor": "6366f1",
        "title": f"{event.replace('_', ' ').title()}: {ticket_id}",
        "text": f"**{subject}**\nStatus: {data.get('status')}  Priority: {data.get('priority')}",
    }


def _build_generic_payload(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


def _build_payload(channel_type: str, event: str, data: dict) -> dict:
    if channel_type == "slack":
        return _build_slack_payload(event, data)
    if channel_type == "teams":
        return _build_teams_payload(event, data)
    # discord / generic
    return _build_generic_payload(event, data)


async def notify_event(event_name: str, payload: dict, db: AsyncSession) -> None:
    """
    Find all active NotificationChannel records subscribed to *event_name*
    and POST a formatted message to each webhook URL.
    """
    from app.models.feature_models import NotificationChannel

    stmt = select(NotificationChannel).where(NotificationChannel.is_active == True)
    result = await db.execute(stmt)
    channels = result.scalars().all()

    targets = [ch for ch in channels if event_name in (ch.events or [])]
    if not targets:
        return

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for ch in targets:
            body = _build_payload(ch.channel_type, event_name, payload)
            try:
                resp = await client.post(
                    ch.webhook_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                logger.debug(
                    f"[notify-ch] '{event_name}' → {ch.channel_type}/{ch.name} [{resp.status_code}]"
                )
            except Exception as exc:
                logger.warning(
                    f"[notify-ch] Failed '{event_name}' → {ch.name} ({ch.channel_type}): {exc}"
                )
