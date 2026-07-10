"""
Microsoft Graph change-notification (webhook) subscription for the inbound mailbox.

Push-based *instant* email → ticket: Graph calls our /graph/notifications endpoint
the moment a new message lands in the inbox, which triggers an immediate poll
(reusing the existing, tested email-poller logic). The 30s poller stays as a
fallback so nothing is ever missed.

No DB migration needed: the subscription is looked up from Graph by its
notificationUrl, and the clientState secret is derived deterministically from
SECRET_KEY (so incoming notifications can be verified without storing anything).
"""
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.admin import EmailConfig
from app.models.inbound_email import InboundEmailConfig

logger = logging.getLogger(__name__)
settings = get_settings()

# When we last ensured/renewed (in-memory throttle so we hit Graph at most ~daily)
_last_ensured: datetime | None = None

GRAPH = "https://graph.microsoft.com/v1.0"
# Outlook message subscriptions max out around 4230 minutes (~2.9 days);
# use a little under that and renew well before expiry.
_SUB_MINUTES = 4000


def client_state() -> str:
    """Deterministic secret used to verify incoming notifications are really ours."""
    return hmac.new(settings.SECRET_KEY.encode(), b"graph-mail-subscription", hashlib.sha256).hexdigest()


def notification_url() -> str:
    return f"{settings.BACKEND_URL.rstrip('/')}/graph/notifications"


async def _token(db: AsyncSession) -> str:
    from app.services.email_poller import _get_m365_graph_token
    cfg = (await db.execute(select(EmailConfig))).scalar_one_or_none()
    if not cfg:
        raise RuntimeError("Email (M365) is not configured — set it up under Admin → Email → Outbound first.")
    tok = await _get_m365_graph_token(cfg, db)
    await db.commit()
    return tok


async def _resource(db: AsyncSession) -> str:
    ic = (await db.execute(select(InboundEmailConfig))).scalar_one_or_none()
    mailbox = ic.graph_mailbox if ic else None
    if not mailbox:
        raise RuntimeError("Inbound mailbox not configured — set it under Admin → Email → Inbound.")
    return f"/users/{mailbox}/mailFolders/inbox/messages"


async def _find_existing(token: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20, headers={"Authorization": f"Bearer {token}"}) as c:
        r = await c.get(f"{GRAPH}/subscriptions")
        if r.status_code != 200:
            return None
        for s in r.json().get("value", []):
            if s.get("notificationUrl") == notification_url():
                return s
    return None


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=_SUB_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


async def ensure(db: AsyncSession) -> dict:
    """Create the subscription if missing, or renew it if it already exists."""
    token = await _token(db)
    resource = await _resource(db)
    existing = await _find_existing(token)
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=25, headers=hdrs) as c:
        if existing:
            r = await c.patch(f"{GRAPH}/subscriptions/{existing['id']}", json={"expirationDateTime": _expiry()})
            if r.status_code >= 400:
                raise RuntimeError(f"Could not renew subscription: {r.text[:250]}")
            return {"status": "renewed", "id": existing["id"], "expires": r.json().get("expirationDateTime")}
        # Create — Graph immediately calls our notificationUrl with a validationToken,
        # so the /graph/notifications endpoint must already be deployed & reachable.
        body = {
            "changeType": "created",
            "notificationUrl": notification_url(),
            "resource": resource,
            "expirationDateTime": _expiry(),
            "clientState": client_state(),
        }
        r = await c.post(f"{GRAPH}/subscriptions", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"Could not create subscription: {r.text[:350]}")
        return {"status": "created", "id": r.json().get("id"), "expires": r.json().get("expirationDateTime")}


async def status(db: AsyncSession) -> dict:
    token = await _token(db)
    s = await _find_existing(token)
    if not s:
        return {"active": False, "notification_url": notification_url()}
    return {"active": True, "id": s.get("id"), "expires": s.get("expirationDateTime"), "notification_url": notification_url()}


async def disable(db: AsyncSession) -> dict:
    token = await _token(db)
    s = await _find_existing(token)
    if not s:
        return {"status": "not_found"}
    async with httpx.AsyncClient(timeout=20, headers={"Authorization": f"Bearer {token}"}) as c:
        await c.delete(f"{GRAPH}/subscriptions/{s['id']}")
    return {"status": "deleted"}


async def maybe_renew() -> None:
    """
    Opportunistic auto-renew: called from the webhook handler and the poller loop.
    Ensures/renews the Graph subscription, but hits Graph at most once every ~24h
    (in-memory throttle) so it's cheap. This keeps the subscription alive forever
    as long as the mailbox sees activity, without needing a background scheduler.
    """
    global _last_ensured
    now = datetime.now(timezone.utc)
    if _last_ensured is not None and (now - _last_ensured) < timedelta(hours=24):
        return
    try:
        async with AsyncSessionLocal() as db:
            # Only bother if inbound email is actually configured for Graph
            ic = (await db.execute(select(InboundEmailConfig))).scalar_one_or_none()
            if not ic or not ic.enabled or not getattr(ic, "graph_mailbox", None):
                return
            await ensure(db)
        _last_ensured = now
        logger.info("Graph subscription auto-renewed")
    except Exception as e:
        logger.warning("Graph subscription auto-renew failed: %s", e)
