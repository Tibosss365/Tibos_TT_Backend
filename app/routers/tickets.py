import csv
import io
import math
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, or_, update, delete
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user
from app.database import get_db
from app.models.ticket import (
    Ticket,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    TicketTimeline,
    TimelineType,
)
from app.models.admin import SLAConfig
from app.models.user import User, UserRole
from app.redis_client import get_redis
from app.schemas.ticket import (
    AddCommentRequest,
    BulkTicketAction,
    PaginatedTickets,
    TicketCreate,
    TicketListOut,
    TicketOut,
    TicketUpdate,
)
from app.services import cache_service
from app.services.notification_service import (
    broadcast_ticket_event,
    notify_ticket_assigned,
    notify_ticket_created,
    notify_ticket_resolved,
)

router = APIRouter(prefix="/tickets", tags=["tickets"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ticket_query(db: AsyncSession):
    return (
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.timeline).selectinload(TicketTimeline.author),
        )
    )


def _calc_biz_due_at(start: datetime, hours: int, work_days: list, work_start: str, work_end: str) -> datetime:
    """
    Advance `start` by `hours` worth of business-hours time.
    work_days: list of ints 0=Mon … 6=Sun
    work_start / work_end: "HH:MM" strings (local, treated as UTC for simplicity)
    """
    ws_h, ws_m = int(work_start.split(":")[0]), int(work_start.split(":")[1])
    we_h, we_m = int(work_end.split(":")[0]),   int(work_end.split(":")[1])
    biz_seconds_per_day = ((we_h * 60 + we_m) - (ws_h * 60 + ws_m)) * 60
    remaining = timedelta(hours=hours)
    current = start

    # Snap to next business moment if currently outside business hours
    def snap_to_biz(dt: datetime) -> datetime:
        for _ in range(14):  # max 2 weeks scan
            if dt.weekday() not in work_days:
                dt = (dt + timedelta(days=1)).replace(hour=ws_h, minute=ws_m, second=0, microsecond=0)
                continue
            day_start = dt.replace(hour=ws_h, minute=ws_m, second=0, microsecond=0)
            day_end   = dt.replace(hour=we_h, minute=we_m, second=0, microsecond=0)
            if dt < day_start:
                return day_start
            if dt >= day_end:
                dt = (dt + timedelta(days=1)).replace(hour=ws_h, minute=ws_m, second=0, microsecond=0)
                continue
            return dt
        return dt

    current = snap_to_biz(current)

    while remaining > timedelta(0):
        if current.weekday() not in work_days:
            current = snap_to_biz(current)
            continue
        day_end = current.replace(hour=we_h, minute=we_m, second=0, microsecond=0)
        time_left_today = day_end - current
        if time_left_today <= timedelta(0):
            current = snap_to_biz(current + timedelta(seconds=1))
            continue
        if remaining <= time_left_today:
            current += remaining
            remaining = timedelta(0)
        else:
            remaining -= time_left_today
            current = snap_to_biz(day_end + timedelta(seconds=1))

    return current


async def _get_sla_due_at(priority: TicketPriority, db: AsyncSession, start: datetime | None = None) -> datetime:
    """Calculate SLA deadline from priority and configured SLA settings."""
    result = await db.execute(select(SLAConfig).limit(1))
    cfg = result.scalar_one_or_none()
    hours_map = {
        TicketPriority.critical: cfg.critical_hours if cfg else 1,
        TicketPriority.high:     cfg.high_hours     if cfg else 4,
        TicketPriority.medium:   cfg.medium_hours   if cfg else 8,
        TicketPriority.low:      cfg.low_hours      if cfg else 24,
    }
    hours = hours_map[priority]
    from_dt = start or datetime.now(timezone.utc)

    if cfg and cfg.countdown_mode == "business_hours":
        return _calc_biz_due_at(from_dt, hours, cfg.work_days or [0,1,2,3,4], cfg.work_start or "09:00", cfg.work_end or "20:00")

    return from_dt + timedelta(hours=hours)


async def _get_ticket_or_404(ticket_id: uuid.UUID, db: AsyncSession) -> Ticket:
    result = await db.execute(
        _ticket_query(db).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


async def _get_admins(db: AsyncSession) -> list[User]:
    res = await db.execute(select(User).where(User.role == UserRole.admin, User.is_active == True))
    return list(res.scalars().all())


def _apply_filters(stmt, search, status_f, priority_f, category_f, assignee_id):
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                Ticket.subject.ilike(like),
                Ticket.submitter_name.ilike(like),
                Ticket.company.ilike(like),
            )
        )
    if status_f:
        stmt = stmt.where(Ticket.status == status_f)
    if priority_f:
        stmt = stmt.where(Ticket.priority == priority_f)
    if category_f:
        stmt = stmt.where(Ticket.category == category_f)
    if assignee_id:
        stmt = stmt.where(Ticket.assignee_id == assignee_id)
    return stmt


def _apply_sort(stmt, sort: str):
    if sort == "oldest":
        return stmt.order_by(Ticket.created_at.asc())
    elif sort == "priority":
        return stmt.order_by(
            func.array_position(
                pg_array(["critical", "high", "medium", "low"]),
                Ticket.priority.cast("text"),
            )
        )
    elif sort == "updated":
        return stmt.order_by(Ticket.updated_at.desc())
    else:  # newest (default)
        return stmt.order_by(Ticket.created_at.desc())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=PaginatedTickets)
async def list_tickets(
    search: str | None = Query(None),
    status: TicketStatus | None = Query(None),
    priority: TicketPriority | None = Query(None),
    category: TicketCategory | None = Query(None),
    assignee_id: uuid.UUID | None = Query(None),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    redis = await get_redis()
    cache_params = {
        "search": search, "status": status, "priority": priority,
        "category": category, "assignee_id": str(assignee_id) if assignee_id else None,
        "sort": sort, "page": page, "page_size": page_size,
    }
    cache_key = cache_service.ticket_list_key(cache_params)
    cached = await cache_service.cache_get(redis, cache_key)
    if cached:
        return cached

    count_stmt = select(func.count()).select_from(Ticket)
    count_stmt = _apply_filters(count_stmt, search, status, priority, category, assignee_id)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    stmt = _apply_filters(stmt, search, status, priority, category, assignee_id)
    stmt = _apply_sort(stmt, sort)

    result = await db.execute(stmt)
    tickets = result.scalars().all()

    response = PaginatedTickets(
        items=[TicketListOut.model_validate(t) for t in tickets],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )
    await cache_service.cache_set(redis, cache_key, response.model_dump(), cache_service.LIST_TTL)
    return response


@router.get("/mine", response_model=PaginatedTickets)
async def my_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count_res = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.assignee_id == current_user.id)
    )
    total = count_res.scalar_one()

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .where(Ticket.assignee_id == current_user.id)
        .order_by(Ticket.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tickets = result.scalars().all()
    return PaginatedTickets(
        items=[TicketListOut.model_validate(t) for t in tickets],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


@router.get("/export")
async def export_csv(
    search: str | None = Query(None),
    status: TicketStatus | None = Query(None),
    priority: TicketPriority | None = Query(None),
    category: TicketCategory | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Ticket).options(selectinload(Ticket.assignee))
    stmt = _apply_filters(stmt, search, status, priority, category, None)
    stmt = stmt.order_by(Ticket.created_at.desc())
    result = await db.execute(stmt)
    tickets = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ticket ID", "Subject", "Category", "Priority", "Status",
        "Assignee", "Submitter", "Company", "Email", "Created", "Updated",
    ])
    for t in tickets:
        writer.writerow([
            t.ticket_id, t.subject, t.category.value, t.priority.value,
            t.status.value,
            t.assignee.name if t.assignee else "Unassigned",
            t.submitter_name, t.company, t.email,
            t.created_at.isoformat(), t.updated_at.isoformat(),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tickets.csv"},
    )


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: TicketCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Fetch SLA config to check timer_start setting
    _sla_cfg_res = await db.execute(select(SLAConfig).limit(1))
    _sla_cfg = _sla_cfg_res.scalar_one_or_none()
    timer_start_mode = _sla_cfg.timer_start if _sla_cfg else "on_creation"

    # SLA starts on creation unless configured to start on assignment
    if timer_start_mode == "on_creation" or body.assignee_id:
        sla_due_at = await _get_sla_due_at(body.priority, db)
    else:
        sla_due_at = None  # will be set when assignee is added

    # Auto set in-progress when ticket is created with an assignee
    initial_status = TicketStatus.in_progress if body.assignee_id else TicketStatus.open

    ticket = Ticket(
        subject=body.subject,
        category=body.category,
        priority=body.priority,
        status=initial_status,
        submitter_name=body.submitter_name,
        company=body.company,
        contact_name=body.contact_name,
        email=body.email,
        phone=body.phone,
        asset=body.asset,
        description=body.description,
        assignee_id=body.assignee_id,
        sla_due_at=sla_due_at,
    )
    db.add(ticket)
    await db.flush()

    # Initial timeline entry
    entry = TicketTimeline(
        ticket_id=ticket.id,
        type=TimelineType.created,
        text=f"Ticket created by <strong>{current_user.name}</strong>",
        author_id=current_user.id,
    )
    db.add(entry)

    if body.assignee_id:
        assign_entry = TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.assign,
            text=f"Assigned by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        )
        db.add(assign_entry)
        status_entry = TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.status,
            text=f"Status changed to <strong>in-progress</strong> by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        )
        db.add(status_entry)

    await db.flush()
    await db.refresh(ticket)

    # Reload with relationships
    full = await _get_ticket_or_404(ticket.id, db)

    # Notifications
    admins = await _get_admins(db)
    await notify_ticket_created(db, full, admins)

    if body.assignee_id and body.assignee_id != current_user.id:
        assignee_res = await db.execute(select(User).where(User.id == body.assignee_id))
        assignee = assignee_res.scalar_one_or_none()
        if assignee:
            await notify_ticket_assigned(db, full, assignee, current_user.name)

    # Invalidate cache
    redis = await get_redis()
    await cache_service.invalidate_tickets(redis)

    # Broadcast
    await broadcast_ticket_event(
        "ticket_created",
        {"ticket_id": str(ticket.id), "ticket_number": full.ticket_id},
        actor_user_id=str(current_user.id),
    )

    return TicketOut.model_validate(full)


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return TicketOut.model_validate(await _get_ticket_or_404(ticket_id, db))


@router.patch("/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: uuid.UUID,
    body: TicketUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    old_assignee_id = ticket.assignee_id
    old_status = ticket.status

    update_data = body.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(ticket, key, val)

    now = datetime.now(timezone.utc)
    ticket.updated_at = now

    # Load SLA config once for use in multiple rules below
    _sla_res = await db.execute(select(SLAConfig).limit(1))
    _sla_cfg = _sla_res.scalar_one_or_none()
    pause_on_statuses = set(_sla_cfg.pause_on) if _sla_cfg and _sla_cfg.pause_on else {"on-hold"}
    timer_start_mode  = _sla_cfg.timer_start if _sla_cfg else "on_creation"

    # ── SLA: pause / resume based on configurable pause_on statuses ───────
    if "status" in update_data:
        new_status_val = update_data["status"]
        new_status_str = new_status_val.value if hasattr(new_status_val, "value") else str(new_status_val)
        old_status_str = old_status.value if hasattr(old_status, "value") else str(old_status)

        entering_pause  = new_status_str in pause_on_statuses and old_status_str not in pause_on_statuses
        leaving_pause   = old_status_str in pause_on_statuses and new_status_str not in pause_on_statuses

        if entering_pause:
            ticket.sla_paused_at = now
        elif leaving_pause:
            # Resume: extend deadline by the time spent paused
            if ticket.sla_paused_at is not None and ticket.sla_due_at is not None:
                paused_since = ticket.sla_paused_at
                if paused_since.tzinfo is None:
                    paused_since = paused_since.replace(tzinfo=timezone.utc)
                ticket.sla_due_at = ticket.sla_due_at + (now - paused_since)
            ticket.sla_paused_at = None

    # ── SLA: recalculate when priority changes ─────────────────────────────
    if "priority" in update_data:
        ticket.sla_due_at = await _get_sla_due_at(update_data["priority"], db)

    # ── Auto in-progress + start SLA timer when assignee assigned ──────────
    if (
        "assignee_id" in update_data
        and update_data["assignee_id"] is not None
        and old_status == TicketStatus.open
        and "status" not in update_data
    ):
        ticket.status = TicketStatus.in_progress
        update_data["status"] = TicketStatus.in_progress
        # If timer_start = "on_assignment" and no SLA deadline set yet, set it now
        if timer_start_mode == "on_assignment" and ticket.sla_due_at is None:
            ticket.sla_due_at = await _get_sla_due_at(ticket.priority, db)

    # Timeline entries for meaningful changes
    if "status" in update_data:
        new_status = update_data["status"]
        entry = TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.status,
            text=f"Status changed to <strong>{new_status.value}</strong> by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        )
        db.add(entry)

        if new_status == TicketStatus.resolved:
            resolve_entry = TicketTimeline(
                ticket_id=ticket.id,
                type=TimelineType.resolved,
                text=f"Ticket resolved by <strong>{current_user.name}</strong>",
                author_id=current_user.id,
            )
            db.add(resolve_entry)

    if "assignee_id" in update_data and update_data["assignee_id"] != old_assignee_id:
        new_assignee_id = update_data["assignee_id"]
        assign_entry = TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.assign,
            text=f"Reassigned by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        )
        db.add(assign_entry)

        if new_assignee_id and new_assignee_id != current_user.id:
            assignee_res = await db.execute(select(User).where(User.id == new_assignee_id))
            assignee = assignee_res.scalar_one_or_none()
            if assignee:
                await notify_ticket_assigned(db, ticket, assignee, current_user.name)

    await db.flush()
    full = await _get_ticket_or_404(ticket_id, db)

    # Notify on resolve
    if "status" in update_data and update_data["status"] == TicketStatus.resolved:
        admins = await _get_admins(db)
        await notify_ticket_resolved(db, full, current_user.name, None, admins)

    redis = await get_redis()
    await cache_service.invalidate_tickets(redis)

    await broadcast_ticket_event(
        "ticket_updated",
        {"ticket_id": str(ticket_id), "ticket_number": full.ticket_id},
        actor_user_id=str(current_user.id),
    )

    return TicketOut.model_validate(full)


@router.post("/{ticket_id}/comments", response_model=TicketOut)
async def add_comment(
    ticket_id: uuid.UUID,
    body: AddCommentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)

    entry = TicketTimeline(
        ticket_id=ticket.id,
        type=TimelineType.comment,
        text=body.text,
        author_id=current_user.id,
    )
    db.add(entry)
    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()

    full = await _get_ticket_or_404(ticket_id, db)

    await broadcast_ticket_event(
        "ticket_comment",
        {"ticket_id": str(ticket_id), "author": current_user.name},
        actor_user_id=str(current_user.id),
    )

    return TicketOut.model_validate(full)


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    await db.delete(ticket)
    redis = await get_redis()
    await cache_service.invalidate_tickets(redis)
    await broadcast_ticket_event("ticket_deleted", {"ticket_id": str(ticket_id)})


@router.post("/bulk", status_code=status.HTTP_200_OK)
async def bulk_action(
    body: BulkTicketAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.action == "delete":
        await db.execute(delete(Ticket).where(Ticket.id.in_(body.ticket_ids)))
    else:
        new_status = TicketStatus.resolved if body.action == "resolve" else TicketStatus.closed
        await db.execute(
            update(Ticket)
            .where(Ticket.id.in_(body.ticket_ids))
            .values(status=new_status, updated_at=datetime.now(timezone.utc))
        )

    redis = await get_redis()
    await cache_service.invalidate_tickets(redis)
    await broadcast_ticket_event(
        "tickets_bulk_updated",
        {"action": body.action, "count": len(body.ticket_ids)},
        actor_user_id=str(current_user.id),
    )
    return {"affected": len(body.ticket_ids)}
