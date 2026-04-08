"""
SLA REST API
============

Endpoints
---------
GET  /sla/overdue              → list all overdue tickets with SLA info
GET  /sla/{ticket_id}          → SLA status for one ticket
POST /sla/{ticket_id}/start    → manually start SLA (e.g. after late assignment)
POST /sla/{ticket_id}/pause    → manually pause SLA
POST /sla/{ticket_id}/resume   → manually resume SLA
POST /sla/{ticket_id}/stop     → manually stop SLA (mark completed)
POST /sla/check-breaches       → trigger breach detection on demand
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user
from app.database import get_db
from app.models.ticket import SLAStatus, Ticket, TicketStatus
from app.models.user import User
from app.services.sla_service import SLAService, sla_breach_detector

router = APIRouter(prefix="/sla", tags=["sla"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_ticket(ticket_id: uuid.UUID, db: AsyncSession) -> Ticket:
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


def _ticket_sla_summary(t: Ticket) -> dict:
    """Ticket summary with SLA status info merged in."""
    return {
        "id":       str(t.id),
        "ticket_id": t.ticket_id,
        "subject":  t.subject,
        "priority": t.priority.value,
        "status":   t.status.value,
        "assignee": t.assignee.name if t.assignee else None,
        **SLAService.get_status_info(t),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/overdue")
async def list_overdue_tickets(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Return all tickets currently marked as overdue.

    Example response:
    {
      "count": 2,
      "tickets": [
        {
          "ticket_id": "TKT-0042",
          "subject": "Cannot login to VPN",
          "sla_status": "overdue",
          "sla_overdue_seconds": 7815,
          "sla_overdue_display": "2h 10m overdue",
          ...
        }
      ]
    }
    """
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .where(Ticket.sla_status == SLAStatus.overdue)
        .order_by(Ticket.sla_due_time.asc())
    )
    tickets = result.scalars().all()
    return {
        "count": len(tickets),
        "tickets": [_ticket_sla_summary(t) for t in tickets],
    }


@router.get("/summary")
async def sla_summary(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Dashboard-level counts by SLA status.
    """
    from sqlalchemy import func

    rows = await db.execute(
        select(Ticket.sla_status, func.count().label("count"))
        .group_by(Ticket.sla_status)
    )
    counts = {row.sla_status.value: row.count for row in rows}
    return {
        "not_started": counts.get("not_started", 0),
        "active":      counts.get("active",      0),
        "paused":      counts.get("paused",       0),
        "overdue":     counts.get("overdue",      0),
        "completed":   counts.get("completed",    0),
    }


@router.get("/{ticket_id}")
async def get_sla_status(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Return SLA status details for a single ticket.

    Example response:
    {
      "sla_status": "active",
      "sla_start_time": "2026-04-07T09:00:00+00:00",
      "sla_due_time":   "2026-04-07T13:00:00+00:00",
      "sla_remaining_seconds": 7200,
      "sla_remaining_display": "2h 00m",
      "sla_overdue_seconds": 0,
      "sla_overdue_display": null,
      "sla_paused_seconds": 0,
      "is_overdue": false,
      "is_paused": false,
      "is_completed": false
    }
    """
    ticket = await _get_ticket(ticket_id, db)
    return _ticket_sla_summary(ticket)


@router.post("/{ticket_id}/start")
async def start_sla(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually start SLA for a ticket (e.g. after retroactive assignment)."""
    ticket = await _get_ticket(ticket_id, db)
    if not ticket.assignee_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot start SLA: ticket is not assigned to an agent",
        )
    await SLAService.start(ticket, db)
    await db.commit()
    return _ticket_sla_summary(ticket)


@router.post("/{ticket_id}/pause")
async def pause_sla(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Manually pause SLA for a ticket."""
    ticket = await _get_ticket(ticket_id, db)
    await SLAService.pause(ticket, db)
    await db.commit()
    return _ticket_sla_summary(ticket)


@router.post("/{ticket_id}/resume")
async def resume_sla(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Manually resume a paused SLA."""
    ticket = await _get_ticket(ticket_id, db)
    await SLAService.resume(ticket, db)
    await db.commit()
    return _ticket_sla_summary(ticket)


@router.post("/{ticket_id}/stop")
async def stop_sla(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Manually stop (complete) SLA for a ticket."""
    ticket = await _get_ticket(ticket_id, db)
    await SLAService.stop(ticket, db)
    await db.commit()
    return _ticket_sla_summary(ticket)


@router.post("/check-breaches")
async def trigger_breach_check(
    _: User = Depends(get_current_user),
):
    """
    Manually trigger the SLA breach detection job.
    Returns the number of tickets newly marked overdue.
    """
    count = await sla_breach_detector.check_breaches()
    return {"breaches_detected": count}
