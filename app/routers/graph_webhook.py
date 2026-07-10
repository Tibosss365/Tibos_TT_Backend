"""
Microsoft Graph webhook — instant email → ticket.

  POST/GET /graph/notifications   (public — Graph calls this)
      • validation handshake: echo the validationToken
      • change notification: verify clientState → trigger an immediate poll

  Admin control (admin JWT):
      POST /inbound-email/enable-webhook   → create/renew the Graph subscription
      POST /inbound-email/disable-webhook  → delete it
      GET  /inbound-email/webhook-status   → current subscription state
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.database import get_db
from app.models.user import User
from app.services import graph_subscription as gs

logger = logging.getLogger(__name__)

# Public webhook (no auth — Graph verifies via clientState)
public_router = APIRouter(tags=["graph-webhook"])
# Admin controls
admin_router = APIRouter(prefix="/inbound-email", tags=["graph-webhook-admin"])


@public_router.api_route("/graph/notifications", methods=["POST", "GET"])
async def graph_notifications(request: Request):
    # 1) Subscription validation handshake — Graph sends ?validationToken=...
    token = request.query_params.get("validationToken")
    if token is not None:
        return PlainTextResponse(content=token, status_code=200)

    # 2) Change notification — verify it's genuinely ours, then poll now
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=202)

    expected = gs.client_state()
    notifications = body.get("value", []) if isinstance(body, dict) else []
    genuine = any(n.get("clientState") == expected for n in notifications)

    if genuine:
        from app.services.email_poller import email_poller
        try:
            await email_poller.poll_once()  # fetch the new mail immediately → create ticket
        except Exception as e:
            logger.error("Graph webhook: poll_once failed: %s", e)
    else:
        logger.warning("Graph webhook: notification with bad/missing clientState — ignored")

    # Always ACK fast so Graph doesn't retry/disable the subscription
    return Response(status_code=202)


@admin_router.post("/enable-webhook", summary="Create/renew the Graph instant-delivery subscription")
async def enable_webhook(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return await gs.ensure(db)


@admin_router.post("/disable-webhook", summary="Remove the Graph subscription (back to polling)")
async def disable_webhook(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return await gs.disable(db)


@admin_router.get("/webhook-status", summary="Graph instant-delivery subscription status")
async def webhook_status(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    return await gs.status(db)
