"""
Inbound email (email-to-ticket) administration routes.

GET  /inbound-email          → get config
PUT  /inbound-email          → create or update config
POST /inbound-email/poll     → manually trigger one poll cycle
GET  /inbound-email/logs     → paginated email-to-ticket log
DELETE /inbound-email/logs   → clear the log
"""
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.inbound_email import EmailTicketLog, InboundEmailConfig
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.inbound_email import (
    EmailLogPage,
    EmailTicketLogOut,
    InboundEmailConfigOut,
    InboundEmailConfigUpdate,
    PollResult,
)
from app.services.email_poller import email_poller

router = APIRouter(prefix="/inbound-email", tags=["inbound-email"])


# ── Config ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=InboundEmailConfigOut)
async def get_inbound_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(InboundEmailConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Inbound email not configured yet")
    return InboundEmailConfigOut.model_validate(cfg)


@router.put("", response_model=InboundEmailConfigOut)
async def upsert_inbound_config(
    body: InboundEmailConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(InboundEmailConfig))
    cfg: InboundEmailConfig | None = result.scalar_one_or_none()

    if cfg is None:
        cfg = InboundEmailConfig()
        db.add(cfg)

    update_data = body.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(cfg, key, val)

    await db.flush()
    await db.refresh(cfg)

    # Restart poller to pick up new interval / enabled state
    if cfg.enabled:
        email_poller.start()
    else:
        email_poller.stop()

    return InboundEmailConfigOut.model_validate(cfg)


# ── Manual poll ────────────────────────────────────────────────────────────────

@router.post("/poll", response_model=PollResult)
async def manual_poll(
    _: User = Depends(require_admin),
):
    """
    Trigger an immediate poll cycle regardless of the schedule.
    Useful for testing or recovering missed emails.
    """
    try:
        result = await email_poller.poll_once()
        return PollResult(**result)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Poll failed: {e}")


# ── Logs ───────────────────────────────────────────────────────────────────────

@router.get("/logs", response_model=EmailLogPage)
async def get_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    count_res = await db.execute(select(func.count()).select_from(EmailTicketLog))
    total = count_res.scalar_one()

    result = await db.execute(
        select(EmailTicketLog)
        .order_by(EmailTicketLog.processed_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    items: list[EmailTicketLogOut] = []
    for log in logs:
        out = EmailTicketLogOut.model_validate(log)
        # Attach human-readable ticket number if available
        if log.ticket_id:
            t_res = await db.execute(select(Ticket).where(Ticket.id == log.ticket_id))
            ticket = t_res.scalar_one_or_none()
            if ticket:
                out.ticket_number = ticket.ticket_id
        items.append(out)

    return EmailLogPage(items=items, total=total)


@router.delete("/logs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from sqlalchemy import delete
    await db.execute(delete(EmailTicketLog))
