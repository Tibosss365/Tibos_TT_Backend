"""
Microsoft Teams two-way chat integration.

  Public (Bot Framework calls this — verified via signed JWT, not app auth)
  ─────────────────────────────────────────────────────────────────────────
  POST /api/messages   → inbound Teams activity (message / conversationUpdate)

  Admin config (admin JWT required)
  ─────────────────────────────────────────────────────────────────────────
  GET  /admin/teams          → current config (app_password masked)
  PUT  /admin/teams          → save config
  POST /admin/teams/test     → verify the bot credentials can fetch a token

See the Teams tab in the admin UI for the full Azure Bot setup walkthrough.
"""
import html
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import require_admin
from app.database import get_db
from app.models.admin import TicketSettings
from app.models.teams import TeamsConfig, TeamsConversation
from app.models.ticket import Ticket, TicketTimeline, TimelineType
from app.models.user import User
from app.schemas.teams import TeamsConfigOut, TeamsConfigUpdate, TeamsTestResult
from app.services import teams_bot as bot
from app.services.notification_service import broadcast_ticket_event
from app.services.sla_service import SLAService

logger = logging.getLogger(__name__)

public_router = APIRouter(tags=["teams-bot"])
admin_router = APIRouter(prefix="/admin/teams", tags=["teams-bot-admin"])


def _messaging_endpoint() -> str:
    return f"{get_settings().BACKEND_URL.rstrip('/')}/api/messages"


async def _get_config(db: AsyncSession) -> TeamsConfig | None:
    res = await db.execute(select(TeamsConfig).limit(1))
    return res.scalar_one_or_none()


# ── Admin config ───────────────────────────────────────────────────────────────

@admin_router.get("", response_model=TeamsConfigOut)
async def get_teams_config(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    cfg = await _get_config(db)
    if not cfg:
        cfg = TeamsConfig(id=1)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return TeamsConfigOut(
        enabled=cfg.enabled,
        tenant_id=cfg.tenant_id,
        app_id=cfg.app_id,
        app_password_set=bool(cfg.app_password),
        default_category=cfg.default_category,
        default_priority=cfg.default_priority,
        messaging_endpoint=_messaging_endpoint(),
        updated_at=cfg.updated_at,
    )


@admin_router.put("", response_model=TeamsConfigOut)
async def update_teams_config(
    body: TeamsConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    cfg = await _get_config(db)
    if not cfg:
        cfg = TeamsConfig(id=1)
        db.add(cfg)

    cfg.enabled = body.enabled
    cfg.tenant_id = body.tenant_id
    cfg.app_id = body.app_id
    if body.app_password:  # blank = "leave unchanged" (mirrors SSO client_secret behaviour)
        cfg.app_password = body.app_password
    cfg.default_category = body.default_category
    cfg.default_priority = body.default_priority

    await db.commit()
    await db.refresh(cfg)
    return TeamsConfigOut(
        enabled=cfg.enabled,
        tenant_id=cfg.tenant_id,
        app_id=cfg.app_id,
        app_password_set=bool(cfg.app_password),
        default_category=cfg.default_category,
        default_priority=cfg.default_priority,
        messaging_endpoint=_messaging_endpoint(),
        updated_at=cfg.updated_at,
    )


@admin_router.post("/test", response_model=TeamsTestResult)
async def test_teams_config(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    cfg = await _get_config(db)
    if not cfg or not (cfg.tenant_id and cfg.app_id and cfg.app_password):
        return TeamsTestResult(ok=False, message="Tenant ID, App ID and App Password are all required.")
    try:
        await bot._get_bot_token(cfg.tenant_id, cfg.app_id, cfg.app_password)
        return TeamsTestResult(ok=True, message="Bot Framework accepted the credentials — token issued successfully.")
    except Exception as e:
        logger.warning("Teams test failed: %s", e)
        return TeamsTestResult(ok=False, message=f"Could not get a token: {e}")


# ── Bot Framework messaging endpoint (public — verified via signed JWT) ───────

@public_router.post("/api/messages")
async def bot_messages(request: Request, db: AsyncSession = Depends(get_db)):
    cfg = await _get_config(db)
    if not cfg or not cfg.enabled or not cfg.app_id or not cfg.app_password or not cfg.tenant_id:
        # Bot not configured yet — ack quietly so Bot Framework doesn't retry.
        return Response(status_code=200)

    if not await bot.verify_bot_request(request.headers.get("Authorization"), cfg.app_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        activity = await request.json()
    except Exception:
        return Response(status_code=202)

    activity_type = activity.get("type")
    conversation = activity.get("conversation") or {}
    conversation_id = conversation.get("id")
    service_url = activity.get("serviceUrl")
    from_ = activity.get("from") or {}
    aad_user_id = from_.get("id")
    aad_user_name = from_.get("name") or "Teams User"
    channel_tenant_id = ((activity.get("channelData") or {}).get("tenant") or {}).get("id")

    async def _reply(text: str):
        try:
            await bot.reply_to_activity(
                tenant_id=cfg.tenant_id, app_id=cfg.app_id, app_password=cfg.app_password,
                incoming_activity=activity, text=text,
            )
        except Exception as e:
            logger.warning("Teams bot: reply failed: %s", e)

    if activity_type == "conversationUpdate":
        bot_id = (activity.get("recipient") or {}).get("id")
        members_added = activity.get("membersAdded") or []
        if any(m.get("id") == bot_id for m in members_added):
            await _reply(
                "\U0001F44B Hi! I'm the HelpdeskPro support bot. Send me a message describing "
                "your issue and I'll open a support ticket for you here."
            )
        return Response(status_code=200)

    if activity_type != "message" or not conversation_id or not service_url:
        return Response(status_code=200)

    text = (activity.get("text") or "").strip()
    # Strip the <at>Bot Name</at> mention Teams prepends in channel messages
    text = re.sub(r"^<at[^>]*>.*?</at>\s*", "", text).strip()
    if not text:
        return Response(status_code=200)

    res = await db.execute(
        select(TeamsConversation).where(TeamsConversation.conversation_id == conversation_id)
    )
    conv = res.scalar_one_or_none()

    if conv:
        ticket = await db.get(Ticket, conv.ticket_id)
        if not ticket:
            return Response(status_code=200)
        db.add(TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.comment,
            text=f"<strong>{html.escape(aad_user_name)}</strong> (via Microsoft Teams): {html.escape(text)}",
        ))
        ticket.updated_at = datetime.now(timezone.utc)
        await db.commit()
        try:
            await broadcast_ticket_event("ticket_updated", {"ticket_id": str(ticket.id)})
        except Exception:
            pass
        await _reply(f"✅ Added to ticket {ticket.ticket_id}.")
        return Response(status_code=200)

    # ── No existing mapping → open a new ticket from this message ────────────
    ts_res = await db.execute(select(TicketSettings).limit(1))
    ts = ts_res.scalar_one_or_none()
    number_prefix = (ts.number_prefix.strip().upper() if ts and ts.number_prefix else "TKT")
    number_digits = (ts.number_digits if ts and ts.number_digits else 4)
    default_status = (ts.default_status if ts and ts.default_status else "open")

    subject = text if len(text) <= 120 else text[:117] + "..."
    ticket = Ticket(
        subject=subject,
        category=cfg.default_category,
        priority=cfg.default_priority,
        status=default_status,
        submitter_name=aad_user_name,
        contact_name=aad_user_name,
        description=text,
        source="teams",
        ticket_prefix=number_prefix,
        ticket_number_digits=number_digits,
    )
    db.add(ticket)
    await db.flush()

    db.add(TeamsConversation(
        ticket_id=ticket.id,
        conversation_id=conversation_id,
        service_url=service_url,
        tenant_id=channel_tenant_id,
        aad_user_id=aad_user_id,
        aad_user_name=aad_user_name,
    ))
    db.add(TicketTimeline(
        ticket_id=ticket.id,
        type=TimelineType.created,
        text=f"Ticket auto-created from Microsoft Teams by <strong>{html.escape(aad_user_name)}</strong>",
    ))

    try:
        await SLAService.start(ticket, db, is_assignment=False, start_time=ticket.created_at)
    except Exception as e:
        logger.warning("Teams bot: SLA start failed: %s", e)

    await db.commit()
    try:
        await broadcast_ticket_event(
            "ticket_created",
            {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_id},
        )
    except Exception:
        pass

    await _reply(
        f"\U0001F3AB Got it — I've opened ticket **{ticket.ticket_id}** for you. "
        f"Reply here any time to add more details; our support team will follow up in this chat."
    )
    return Response(status_code=200)
