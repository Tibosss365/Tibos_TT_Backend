"""
Outbound webhook dispatcher.

Fires HTTP POST to all active WebhookConfig records whose `events` list
contains the given event name.  Each delivery is signed with HMAC-SHA256
using the configured secret (if any).
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")

_TIMEOUT = 10.0  # seconds per webhook call


def _sign_payload(secret: str, body: bytes) -> str:
    """Return 'sha256=<hex>' HMAC signature."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


async def dispatch_event(event_name: str, payload: dict, db: AsyncSession) -> None:
    """
    Find all active webhooks subscribed to *event_name* and fire them.
    Runs in the background — failures are logged but not raised.
    """
    from app.models.feature_models import WebhookConfig

    stmt = select(WebhookConfig).where(
        WebhookConfig.is_active == True,
    )
    result = await db.execute(stmt)
    webhooks = result.scalars().all()

    # Filter by event subscription in Python (JSONB `@>` would need psycopg3)
    targets = [wh for wh in webhooks if event_name in (wh.events or [])]
    if not targets:
        return

    body = json.dumps({"event": event_name, "data": payload}, default=str).encode()

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for wh in targets:
            headers = {
                "Content-Type": "application/json",
                "X-Tibos-Event": event_name,
            }
            if wh.secret:
                headers["X-Tibos-Signature"] = _sign_payload(wh.secret, body)

            try:
                resp = await client.post(wh.url, content=body, headers=headers)
                resp.raise_for_status()
                # Update last_triggered_at
                wh.last_triggered_at = datetime.now(timezone.utc)
                logger.debug(f"[webhook] Delivered '{event_name}' → {wh.url} [{resp.status_code}]")
            except Exception as exc:
                logger.warning(f"[webhook] Failed to deliver '{event_name}' → {wh.url}: {exc}")

    await db.commit()
