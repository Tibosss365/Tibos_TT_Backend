import csv
import io
import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, or_, update, delete, String
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_user
from app.database import get_db
from app.models.ticket import (
    Ticket,
    TicketCategory,
    TicketPriority,
    SLAStatus,
    TicketStatus,
    TicketTimeline,
    TimelineType,
)
from app.models.user import User, UserRole
from app.services.sla_service import SLAService
from app.schemas.ticket import (
    AddCommentRequest,
    BulkTicketAction,
    PaginatedTickets,
    TicketCreate,
    TicketListOut,
    TicketOut,
    TicketUpdate,
)
from app.services.notification_service import (
    broadcast_ticket_event,
    notify_ticket_assigned,
    notify_ticket_created,
    notify_ticket_resolved,
)
from app.services.email_sender import send_ticket_email

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


def _apply_filters(
    stmt,
    search,
    status_f,
    priority_f,
    category_f,
    assignee_id,
    sla_status_f=None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
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
    if sla_status_f:
        # Cast to String because the production DB stores sla_status as
        # character varying, not a native PostgreSQL enum type. Without the
        # cast, asyncpg emits "$1::slastatus" which causes an operator error.
        stmt = stmt.where(Ticket.sla_status.cast(String) == sla_status_f.value)
    if date_from:
        stmt = stmt.where(Ticket.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Ticket.created_at <= date_to)
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
    category: str | None = Query(None),
    assignee_id: uuid.UUID | None = Query(None),
    sla_status: SLAStatus | None = Query(None),
    date_from: datetime | None = Query(None, description="Filter tickets created from this datetime (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Filter tickets created up to this datetime (ISO 8601)"),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    # Accept 'limit' as an alias for page_size (used by analytics page)
    limit: int | None = Query(None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    effective_page_size = limit if limit is not None else page_size

    count_stmt = select(func.count()).select_from(Ticket)
    count_stmt = _apply_filters(count_stmt, search, status, priority, category, assignee_id, sla_status, date_from, date_to)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .offset((page - 1) * effective_page_size)
        .limit(effective_page_size)
    )
    stmt = _apply_filters(stmt, search, status, priority, category, assignee_id, sla_status, date_from, date_to)
    stmt = _apply_sort(stmt, sort)

    result = await db.execute(stmt)
    tickets = result.scalars().all()

    return PaginatedTickets(
        items=[TicketListOut.model_validate(t) for t in tickets],
        total=total,
        page=page,
        page_size=effective_page_size,
        pages=math.ceil(total / effective_page_size) if total else 1,
    )


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
    category: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Ticket).options(selectinload(Ticket.assignee))
    stmt = _apply_filters(stmt, search, status, priority, category, None, None, date_from, date_to)
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
            t.ticket_id, t.subject, t.category, t.priority.value,
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
    # Auto set in-progress when ticket is created with an assignee.
    initial_status = TicketStatus.in_progress if body.assignee_id else TicketStatus.open

    ticket = Ticket(
        subject=body.subject,
        category=body.category,
        priority=body.priority,
        status=initial_status.value,
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
        db.add(TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.assign,
            text=f"Assigned by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        ))
        db.add(TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.status,
            text=f"Status changed to <strong>in-progress</strong> by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        ))

    # SLA starts immediately when ticket is created (clock runs from creation)
    await SLAService.start(ticket, db)

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

    # ── Email: ticket created confirmation to submitter ────────────────
    if full.email:
        email_body = (
            f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
            f"<p>Your support request has been received. Here are the details:</p>"
            f"<p><strong>Subject:</strong> {full.subject}<br/>"
            f"<strong>Priority:</strong> {full.priority.value if hasattr(full.priority,'value') else full.priority}<br/>"
            f"<strong>Category:</strong> {full.category}</p>"
            f"<p>We will get back to you as soon as possible. You can reply to this email to add more information.</p>"
        )
        msg_id = await send_ticket_email(
            db, full,
            to_email=full.email,
            subject=f"[{full.ticket_id}] {full.subject}",
            body_html=email_body,
            action_label="New Ticket Created",
            action_color="#6366f1",
        )
        if msg_id:
            # Store thread ID for reply matching and log in timeline
            ticket.email_thread_id = msg_id
            db.add(TicketTimeline(
                ticket_id=ticket.id,
                type=TimelineType.email_out,
                text=f"Ticket confirmation email sent to <strong>{full.email}</strong>",
                author_id=current_user.id,
            ))
            await db.flush()

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
    old_status      = ticket.status

    update_data = body.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(ticket, key, val)

    ticket.updated_at = datetime.now(timezone.utc)

    # ── SLA: handle status transitions ────────────────────────────────────
    if "status" in update_data:
        await SLAService.handle_status_change(
            ticket, update_data["status"], old_status, db
        )

    # ── SLA: recalculate when priority changes ────────────────────────────
    if "priority" in update_data:
        await SLAService.recalculate(ticket, db, new_priority=update_data["priority"])

    # ── Auto in-progress when ticket gets its first assignee ─────────────
    if (
        "assignee_id" in update_data
        and update_data["assignee_id"] is not None
        and old_assignee_id is None
        and old_status == TicketStatus.open
        and "status" not in update_data
    ):
        ticket.status = TicketStatus.in_progress
        update_data["status"] = TicketStatus.in_progress

    # ── SLA: start whenever an assignee is set AND SLA has not started ────
    if (
        "assignee_id" in update_data
        and update_data["assignee_id"] is not None
        and ticket.sla_status == SLAStatus.not_started
    ):
        await SLAService.start(ticket, db)

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

    # ── Email: status-change notifications to submitter ──────────────────
    if "status" in update_data and full.email:
        new_st = update_data["status"]
        email_cfg_data: dict | None = None

        if new_st == TicketStatus.on_hold:
            email_cfg_data = dict(
                subject=f"[{full.ticket_id}] Your ticket is on hold",
                body=(
                    f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
                    f"<p>Your ticket <strong>{full.subject}</strong> has been placed <strong>on hold</strong>.</p>"
                    f"<p>We will resume work on it as soon as possible. "
                    f"Reply to this email if you have additional information.</p>"
                ),
                action_label="Ticket On Hold",
                action_color="#f59e0b",
            )
        elif new_st == TicketStatus.resolved:
            resolution_note = full.resolution or ""
            email_cfg_data = dict(
                subject=f"[{full.ticket_id}] Your ticket has been resolved",
                body=(
                    f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
                    f"<p>Your ticket <strong>{full.subject}</strong> has been <strong>resolved</strong>.</p>"
                    + (f"<p><strong>Resolution:</strong><br/>{resolution_note}</p>" if resolution_note else "")
                    + "<p>If your issue is not fully resolved, reply to this email and we will reopen the ticket.</p>"
                ),
                action_label="Ticket Resolved",
                action_color="#10b981",
            )
        elif new_st == TicketStatus.closed:
            email_cfg_data = dict(
                subject=f"[{full.ticket_id}] Your ticket has been closed",
                body=(
                    f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
                    f"<p>Your ticket <strong>{full.subject}</strong> has been <strong>closed</strong>.</p>"
                    f"<p>Thank you for contacting us. If you need further assistance, please submit a new request.</p>"
                ),
                action_label="Ticket Closed",
                action_color="#6b7280",
            )
        elif new_st == TicketStatus.in_progress and old_status == TicketStatus.open:
            email_cfg_data = dict(
                subject=f"[{full.ticket_id}] Work has started on your ticket",
                body=(
                    f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
                    f"<p>Our team has started working on your ticket <strong>{full.subject}</strong>.</p>"
                    f"<p>We will keep you updated on the progress. "
                    f"Reply to this email if you have additional information.</p>"
                ),
                action_label="Ticket In Progress",
                action_color="#8b5cf6",
            )

        if email_cfg_data:
            await send_ticket_email(
                db, full,
                to_email=full.email,
                subject=email_cfg_data["subject"],
                body_html=email_cfg_data["body"],
                action_label=email_cfg_data["action_label"],
                action_color=email_cfg_data["action_color"],
                in_reply_to=full.email_thread_id,
                references=full.email_thread_id,
            )
            db.add(TicketTimeline(
                ticket_id=full.id,
                type=TimelineType.email_out,
                text=f"Status update email sent to <strong>{full.email}</strong>",
                author_id=current_user.id,
            ))
            await db.flush()

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

    # ── Optionally email the comment to the submitter ─────────────────────
    if body.send_to_customer and ticket.email:
        safe_text = body.text.replace("<", "&lt;").replace(">", "&gt;")
        email_body = (
            f"<p>Hi <strong>{ticket.submitter_name}</strong>,</p>"
            f"<p>A support agent has sent you a message regarding your ticket "
            f"<strong>{ticket.subject}</strong>:</p>"
            f"<blockquote style='border-left:3px solid #6366f1;padding:8px 12px;"
            f"margin:12px 0;background:#f5f5ff;border-radius:4px;"
            f"color:#374151;white-space:pre-wrap'>{safe_text}</blockquote>"
            f"<p>You can reply to this email to respond to the agent.</p>"
        )
        await send_ticket_email(
            db, ticket,
            to_email=ticket.email,
            subject=f"[{ticket.ticket_id}] Update on your ticket",
            body_html=email_body,
            action_label="Agent Message",
            action_color="#6366f1",
            in_reply_to=ticket.email_thread_id,
            references=ticket.email_thread_id,
        )
        db.add(TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.email_out,
            text=f"Comment sent as email to <strong>{ticket.email}</strong> by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        ))
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

    await broadcast_ticket_event(
        "tickets_bulk_updated",
        {"action": body.action, "count": len(body.ticket_ids)},
        actor_user_id=str(current_user.id),
    )
    return {"affected": len(body.ticket_ids)}
