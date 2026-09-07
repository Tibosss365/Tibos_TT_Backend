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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.admin import TicketSettings
from app.models.inbound_email import EmailLogStatus, EmailTicketLog, InboundEmailConfig
from app.models.ticket import Ticket, TicketTimeline, TimelineType
from app.models.user import User, UserRole
from app.schemas.inbound_email import (
    EmailLogPage,
    EmailTicketLogDetail,
    EmailTicketLogOut,
    InboundEmailConfigOut,
    InboundEmailConfigUpdate,
    PollResult,
)
from app.services.email_poller import email_poller
from app.services.sla_service import SLAService

router = APIRouter(prefix="/inbound-email", tags=["inbound-email"])


# ── Config ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=InboundEmailConfigOut)
async def get_inbound_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Return the inbound email configuration.
    Auto-creates a disabled default row on first call so the frontend
    always gets a 200 (same pattern as GET /admin/sla and GET /admin/email).
    """
    result = await db.execute(select(InboundEmailConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = InboundEmailConfig()   # all defaults: enabled=False, basic IMAP, etc.
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)
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
        out.has_body = bool(log.body)
        # Attach human-readable ticket number if available
        if log.ticket_id:
            t_res = await db.execute(select(Ticket).where(Ticket.id == log.ticket_id))
            ticket = t_res.scalar_one_or_none()
            if ticket:
                out.ticket_number = ticket.ticket_id
        items.append(out)

    return EmailLogPage(items=items, total=total)


@router.get("/logs/{log_id}", response_model=EmailTicketLogDetail)
async def get_log_detail(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Single-entry fetch — includes the full email body (for the preview
    shown before a manual convert, or a not-converted entry's detail view)."""
    log = await db.get(EmailTicketLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log entry not found")

    out = EmailTicketLogDetail.model_validate(log)
    out.has_body = bool(log.body)
    if log.ticket_id:
        t_res = await db.execute(select(Ticket).where(Ticket.id == log.ticket_id))
        ticket = t_res.scalar_one_or_none()
        if ticket:
            out.ticket_number = ticket.ticket_id
    return out


@router.delete("/logs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from sqlalchemy import delete
    await db.execute(delete(EmailTicketLog))


@router.delete("/logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log_entry(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Delete one log entry. Only removes the log/audit row — a ticket it
    already converted to (if any) is untouched; delete that separately from
    the ticket list if you actually want it gone."""
    log = await db.get(EmailTicketLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log entry not found")
    await db.delete(log)


@router.post("/logs/{log_id}/convert", response_model=EmailTicketLogOut)
async def convert_log_to_ticket(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually create a ticket from a log entry that wasn't converted
    automatically (filtered / duplicate / error)."""
    if current_user.role == UserRole.user:
        raise HTTPException(status_code=403, detail="Staff only")

    log = await db.get(EmailTicketLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log entry not found")
    if log.ticket_id:
        raise HTTPException(status_code=400, detail="This email was already converted to a ticket")

    inbound = await db.get(InboundEmailConfig, log.inbound_config_id)

    ts_res = await db.execute(select(TicketSettings).limit(1))
    ts = ts_res.scalar_one_or_none()
    number_prefix = (ts.number_prefix.strip().upper() if ts and ts.number_prefix else "TKT")
    number_digits = (ts.number_digits if ts and ts.number_digits else 4)
    default_status = (ts.default_status if ts and ts.default_status else "open")

    company = log.from_email.split("@")[-1].lower() if "@" in log.from_email else ""
    from app.services.account_owner_service import owners_for_company
    ticket_owners = await owners_for_company(db, email=log.from_email, company_name=company)

    ticket = Ticket(
        subject=log.subject or "(no subject)",
        category=(inbound.default_category if inbound else None) or "email",
        priority=(inbound.default_priority if inbound else "medium"),
        status=default_status,
        submitter_name=log.from_name or log.from_email,
        company=company,
        contact_name=log.from_name or log.from_email,
        email=log.from_email,
        description=log.body or f"(Original email content wasn't saved for this entry.)\n\nSubject: {log.subject}",
        source="email",
        ticket_prefix=number_prefix,
        ticket_number_digits=number_digits,
        created_at=log.received_at or datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        owners=ticket_owners,
    )
    db.add(ticket)
    await db.flush()

    await SLAService.start(ticket, db, is_assignment=False, start_time=ticket.created_at)

    db.add(TicketTimeline(
        ticket_id=ticket.id,
        type=TimelineType.created,
        text=(
            f"Ticket manually created from a {log.status.value} email by "
            f"<strong>{current_user.name}</strong>"
        ),
        author_id=current_user.id,
    ))

    log.status = EmailLogStatus.processed
    log.ticket_id = ticket.id
    log.error_message = None

    await db.flush()
    await db.refresh(log)

    out = EmailTicketLogOut.model_validate(log)
    out.has_body = bool(log.body)
    out.ticket_number = ticket.ticket_id
    return out
