import csv
import io
import math
import uuid
from datetime import datetime, timezone

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
    ticket = Ticket(
        subject=body.subject,
        category=body.category,
        priority=body.priority,
        submitter_name=body.submitter_name,
        company=body.company,
        contact_name=body.contact_name,
        email=body.email,
        phone=body.phone,
        asset=body.asset,
        description=body.description,
        assignee_id=body.assignee_id,
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
    ticket.updated_at = datetime.now(timezone.utc)

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
