import csv
import io
import logging
import math
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
from app.models.admin import DomainCompany, TicketSettings
from app.services.sla_service import SLAService
from app.schemas.ticket import (
    AddCommentRequest,
    BulkTicketAction,
    PaginatedTickets,
    TicketCreate,
    TicketDataUpdate,
    TicketListOut,
    TicketOut,
    TicketUpdate,
)
from app.schemas.feature_schemas import DuplicateCheckRequest, DuplicateTicketOut
from app.services.notification_service import (
    broadcast_ticket_event,
    notify_ticket_assigned,
    notify_ticket_created,
    notify_ticket_resolved,
)
from app.services.email_sender import send_ticket_email

router = APIRouter(prefix="/tickets", tags=["tickets"])
logger = logging.getLogger("uvicorn.error")


async def _auto_discover_domain(domain: str, db: AsyncSession) -> str | None:
    """
    Look up a company name for *domain* using Clearbit's free autocomplete API.
    If a name is found, the mapping is saved to domain_companies so future tickets
    from the same domain are resolved instantly (no extra HTTP call needed).
    Returns the company name, or None if nothing was found.
    """
    url = f"https://autocomplete.clearbit.com/v1/companies/suggest?query={domain}"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, headers={"User-Agent": "TibosTT/1.0"})
            if resp.status_code == 200:
                results = resp.json()
                if results:
                    best = results[0]
                    company_name = best.get("name") or ""
                    logo_url = best.get("logo") or None
                    if company_name:
                        # Persist so the next ticket from this domain needs no lookup
                        record = DomainCompany(
                            domain=domain,
                            company_name=company_name,
                            logo_url=logo_url,
                            auto_discovered=True,
                        )
                        db.add(record)
                        # Use flush (not commit) — the outer create_ticket transaction
                        # will commit everything together
                        await db.flush()
                        logger.info(
                            f"[domain-discovery] Auto-saved '{company_name}' for domain '{domain}'"
                        )
                        return company_name
    except Exception as exc:
        logger.warning(f"[domain-discovery] Clearbit lookup failed for '{domain}': {exc}")
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ticket_query(db: AsyncSession):
    """Base query — no attachments (safe before migration 025). Excludes soft-deleted tickets."""
    return (
        select(Ticket)
        .where(Ticket.is_deleted == False)  # noqa: E712
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.timeline).selectinload(TicketTimeline.author),
        )
    )


def _any_ticket_query(db: AsyncSession):
    """Like _ticket_query but includes soft-deleted tickets (for restore / permanent delete)."""
    return (
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.timeline).selectinload(TicketTimeline.author),
        )
    )


def _ticket_detail_query(db: AsyncSession):
    """Full query including attachments — only used by the single-ticket GET endpoint."""
    return (
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.timeline).selectinload(TicketTimeline.author),
            selectinload(Ticket.attachments),
        )
    )


def _inject_empty_attachments(ticket: Ticket) -> None:
    """Inject [] so Pydantic model_validate doesn't trigger an async lazy-load."""
    if "attachments" not in ticket.__dict__:
        ticket.__dict__["attachments"] = []


async def _get_ticket_or_404(ticket_id: uuid.UUID, db: AsyncSession) -> Ticket:
    result = await db.execute(
        _ticket_query(db).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


async def _get_any_ticket_or_404(ticket_id: uuid.UUID, db: AsyncSession) -> Ticket:
    """Fetch ticket regardless of is_deleted — used by restore and permanent-delete endpoints."""
    result = await db.execute(
        _any_ticket_query(db).where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


async def _get_ticket_detail_or_404(ticket_id: uuid.UUID, db: AsyncSession) -> Ticket:
    """Like _get_ticket_or_404 but also eagerly loads attachments."""
    result = await db.execute(
        _ticket_detail_query(db).where(Ticket.id == ticket_id)
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

    count_stmt = select(func.count()).select_from(Ticket).where(Ticket.is_deleted == False)  # noqa: E712
    count_stmt = _apply_filters(count_stmt, search, status, priority, category, assignee_id, sla_status, date_from, date_to)
    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one()

    stmt = (
        select(Ticket)
        .where(Ticket.is_deleted == False)  # noqa: E712
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
        select(func.count()).select_from(Ticket).where(Ticket.assignee_id == current_user.id, Ticket.is_deleted == False)  # noqa: E712
    )
    total = count_res.scalar_one()

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .where(Ticket.assignee_id == current_user.id, Ticket.is_deleted == False)  # noqa: E712
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


@router.get("/my-requests", response_model=PaginatedTickets)
async def my_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Tickets submitted by the current end-user (requester_id = current user)."""
    count_res = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.requester_id == current_user.id, Ticket.is_deleted == False)  # noqa: E712
    )
    total = count_res.scalar_one()

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .where(Ticket.requester_id == current_user.id, Ticket.is_deleted == False)  # noqa: E712
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
    stmt = select(Ticket).where(Ticket.is_deleted == False).options(selectinload(Ticket.assignee))  # noqa: E712
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
    # ── Load ticket settings (prefix, digits, defaults) ──────────────────
    ts_result = await db.execute(select(TicketSettings).limit(1))
    ts = ts_result.scalar_one_or_none()

    number_prefix    = (ts.number_prefix.strip().upper() if ts and ts.number_prefix else "TKT")
    number_digits    = (ts.number_digits if ts and ts.number_digits else 4)
    default_status_str = (ts.default_status if ts and ts.default_status else "open")

    # If a ticket is created with an assignee, always start in-progress.
    # Otherwise use the admin-configured default status.
    if body.assignee_id:
        initial_status = TicketStatus.in_progress
    else:
        try:
            initial_status = TicketStatus(default_status_str)
        except ValueError:
            initial_status = TicketStatus.open

    # For end-users: auto-fill contact info and link as requester
    is_end_user = current_user.role == UserRole.user
    requester_id = current_user.id if is_end_user else None
    submitter_name = body.submitter_name or (current_user.name if is_end_user else body.submitter_name)
    contact_name   = body.contact_name   or (current_user.name if is_end_user else body.contact_name)
    email          = body.email          or (current_user.username if is_end_user and "@" in (current_user.username or "") else body.email)
    # End-users cannot self-assign tickets
    assignee_id    = None if is_end_user else body.assignee_id

    # ── Auto-fill company from domain company registry ────────────────────
    # Priority: 1) value provided by caller  2) DB lookup  3) live Clearbit discovery
    company = body.company
    if not company and email and "@" in email:
        email_domain = email.split("@")[-1].lower().strip()

        # 1. Check the local registry first (fastest path)
        dc_res = await db.execute(
            select(DomainCompany).where(DomainCompany.domain == email_domain)
        )
        dc = dc_res.scalar_one_or_none()
        if dc:
            company = dc.company_name
        else:
            # 2. Not in registry — try live Clearbit lookup and auto-save result
            discovered = await _auto_discover_domain(email_domain, db)
            if discovered:
                company = discovered

    ticket = Ticket(
        subject=body.subject,
        category=body.category,
        priority=body.priority,
        status=initial_status.value,
        ticket_prefix=number_prefix,
        ticket_number_digits=number_digits,
        submitter_name=submitter_name,
        company=company,
        contact_name=contact_name,
        email=email,
        phone=body.phone,
        asset=body.asset,
        description=body.description,
        assignee_id=assignee_id,
        group_id=body.group_id,
        requester_id=requester_id,
        source=body.source or "portal",
        tags=body.tags or [],
        custom_field_data=body.custom_field_data or {},
        due_date=body.due_date,
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

    if assignee_id:
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

    await SLAService.start(
        ticket, db,
        is_assignment=bool(assignee_id),
        start_time=ticket.created_at,
    )

    # ── Automation engine ────────────────────────────────────────────────────
    try:
        from app.services.automation_engine import run_automation
        await run_automation("ticket_created", ticket, db)
    except Exception as _ae:
        logger.warning(f"[automation] ticket_created hook failed: {_ae}")

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
        description_block = ""
        if full.description:
            safe_desc = full.description.replace("<", "&lt;").replace(">", "&gt;")
            description_block = (
                f"<p><strong>Description:</strong><br/>"
                f"<span style='white-space:pre-wrap;color:#374151'>{safe_desc}</span></p>"
            )
        email_body = (
            f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
            f"<p>Your support request has been received. Here are the details:</p>"
            f"<p><strong>Subject:</strong> {full.subject}<br/>"
            f"<strong>Priority:</strong> {full.priority.value if hasattr(full.priority,'value') else full.priority}<br/>"
            f"<strong>Category:</strong> {full.category}</p>"
            + description_block +
            f"<p>We will get back to you as soon as possible. You can reply to this email to add more information.</p>"
        )
        msg_id = await send_ticket_email(
            db, full,
            to_email=full.email,
            subject=f"[{full.ticket_id}] {full.subject}",
            body_html=email_body,
            action_label="New Ticket Created",
            action_color="#6366f1",
            assignee_name=full.assignee.name if full.assignee else None,
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

    # Commit before broadcasting so any SSE-triggered fetchTickets sees the new ticket
    await db.commit()
    try:
        await broadcast_ticket_event(
            "ticket_created",
            {"ticket_id": str(ticket.id), "ticket_number": full.ticket_id},
            actor_user_id=str(current_user.id),
        )
    except Exception:
        pass  # broadcast failure must not undo the committed create

    _inject_empty_attachments(full)
    return TicketOut.model_validate(full)


@router.post("/check-duplicate", response_model=list[DuplicateTicketOut])
async def check_duplicate(
    body: DuplicateCheckRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return open tickets with subjects similar to the given subject (duplicate detection)."""
    from app.services.duplicate_detector import find_duplicates
    dupes = await find_duplicates(body.subject, db)
    return [DuplicateTicketOut.model_validate(t) for t in dupes]


@router.get("/export-pdf")
async def export_pdf_bulk(
    search: str | None = Query(None),
    status: TicketStatus | None = Query(None),
    priority: TicketPriority | None = Query(None),
    category: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Export filtered tickets as a PDF report."""
    stmt = select(Ticket).where(Ticket.is_deleted == False).options(selectinload(Ticket.assignee))  # noqa: E712
    stmt = _apply_filters(stmt, search, status, priority, category, None, None, date_from, date_to)
    stmt = stmt.order_by(Ticket.created_at.desc()).limit(500)
    result = await db.execute(stmt)
    tickets = result.scalars().all()

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=30)
        styles = getSampleStyleSheet()
        elements = []

        elements.append(Paragraph("Ticket Report", styles["Title"]))
        elements.append(Spacer(1, 12))

        headers = ["Ticket ID", "Subject", "Category", "Priority", "Status", "Assignee", "Created"]
        data = [headers]
        for t in tickets:
            data.append([
                t.ticket_id,
                (t.subject[:40] + "…") if len(t.subject) > 40 else t.subject,
                t.category,
                t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                t.status.value if hasattr(t.status, "value") else str(t.status),
                t.assignee.name if t.assignee else "Unassigned",
                t.created_at.strftime("%Y-%m-%d") if t.created_at else "",
            ])

        table = Table(data, colWidths=[70, 150, 70, 55, 65, 80, 65])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6366f1")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f3ff")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        doc.build(elements)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=tickets.pdf"},
        )
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="reportlab is not installed. Run: pip install reportlab",
        )


@router.post("/import", status_code=200)
async def import_csv(
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Import tickets from a CSV file.
    Expects multipart/form-data with a 'file' field containing a CSV.
    CSV columns: subject, category, priority, description, submitter_name, company, email
    """
    from fastapi import UploadFile, File
    raise HTTPException(
        status_code=400,
        detail="Use multipart upload: POST /tickets/import with file field",
    )


@router.post("/import-upload", status_code=200)
async def import_csv_upload(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Placeholder — see import_csv_file endpoint below."""
    raise HTTPException(status_code=400, detail="Use POST /tickets/import-file")


@router.get("/deleted", response_model=PaginatedTickets)
async def list_deleted_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    count_res = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.is_deleted == True)  # noqa: E712
    )
    total = count_res.scalar_one()

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee))
        .where(Ticket.is_deleted == True)  # noqa: E712
        .order_by(Ticket.deleted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tickets = result.scalars().all()
    for t in tickets:
        _inject_empty_attachments(t)
    return PaginatedTickets(
        items=[TicketListOut.model_validate(t) for t in tickets],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return TicketOut.model_validate(await _get_ticket_detail_or_404(ticket_id, db))


@router.get("/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(
    ticket_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.ticket_attachment import TicketAttachment
    from app.services.attachment_storage import get_storage_backend, LocalFileBackend
    from fastapi.responses import RedirectResponse

    result = await db.execute(
        select(TicketAttachment).where(
            TicketAttachment.id == attachment_id,
            TicketAttachment.ticket_id == ticket_id,
        )
    )
    att = result.scalar_one_or_none()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")

    safe_name = att.filename.encode("ascii", errors="replace").decode()
    media_type = att.content_type or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{safe_name}"'}

    # ── Legacy rows: binary stored directly in the DB ─────────────────────
    if att.content is not None:
        return Response(content=att.content, media_type=media_type, headers=headers)

    # ── New rows: file is in object storage ───────────────────────────────
    if not att.storage_key:
        raise HTTPException(status_code=404, detail="Attachment content not available")

    storage = get_storage_backend()

    if isinstance(storage, LocalFileBackend):
        # Local dev: read from disk and stream back through the API
        try:
            content = await storage.read(att.storage_key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Attachment file not found on disk")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read attachment: {exc}")
        return Response(content=content, media_type=media_type, headers=headers)
    else:
        # Cloud backend (Azure / S3): redirect to a short-lived presigned URL
        try:
            url = await storage.presigned_url(att.storage_key, expires_seconds=300)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {exc}")
        return RedirectResponse(url=url, status_code=302)


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

    # ── SLA: start on first agent assignment (handles on_assignment timer_start) ──
    # Anchor start_time to ticket.updated_at (already set above) so the SLA
    # start timestamp exactly matches the update event recorded on the ticket.
    if (
        "assignee_id" in update_data
        and update_data["assignee_id"] is not None
        and ticket.sla_status == SLAStatus.not_started
    ):
        await SLAService.start(ticket, db, is_assignment=True, start_time=ticket.updated_at)

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

        # Build a meaningful timeline message
        if new_assignee_id is None:
            assign_text = f"Unassigned by <strong>{current_user.name}</strong>"
        elif old_assignee_id is None:
            # First assignment — check if self-pickup or admin assign
            if new_assignee_id == current_user.id:
                assign_text = f"Picked up by <strong>{current_user.name}</strong>"
            else:
                assignee_res = await db.execute(select(User).where(User.id == new_assignee_id))
                new_agent = assignee_res.scalar_one_or_none()
                assign_text = (
                    f"Assigned to <strong>{new_agent.name if new_agent else 'agent'}</strong>"
                    f" by <strong>{current_user.name}</strong>"
                )
        else:
            assignee_res = await db.execute(select(User).where(User.id == new_assignee_id))
            new_agent = assignee_res.scalar_one_or_none()
            assign_text = (
                f"Reassigned to <strong>{new_agent.name if new_agent else 'agent'}</strong>"
                f" by <strong>{current_user.name}</strong>"
            )

        assign_entry = TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.assign,
            text=assign_text,
            author_id=current_user.id,
        )
        db.add(assign_entry)

        # Notify the assignee (but not if they assigned it to themselves)
        if new_assignee_id and new_assignee_id != current_user.id:
            assignee_res2 = await db.execute(select(User).where(User.id == new_assignee_id))
            assignee = assignee_res2.scalar_one_or_none()
            if assignee:
                await notify_ticket_assigned(db, ticket, assignee, current_user.name)

    # ── Reopen count ─────────────────────────────────────────────────────────
    if "status" in update_data:
        new_st = update_data["status"]
        if (
            new_st == TicketStatus.open
            and old_status in (TicketStatus.resolved, TicketStatus.closed)
        ):
            ticket.reopen_count = (ticket.reopen_count or 0) + 1

    # ── Automation engine ────────────────────────────────────────────────────
    try:
        from app.services.automation_engine import run_automation
        await run_automation("ticket_updated", ticket, db)
        if "status" in update_data:
            await run_automation("status_changed", ticket, db)
    except Exception as _ae:
        logger.warning(f"[automation] ticket_updated hook failed: {_ae}")

    await db.flush()
    full = await _get_ticket_or_404(ticket_id, db)

    # Notify on resolve + send CSAT survey
    if "status" in update_data and update_data["status"] == TicketStatus.resolved:
        admins = await _get_admins(db)
        await notify_ticket_resolved(db, full, current_user.name, None, admins)
        try:
            from app.services.csat_service import send_csat_survey
            from app.config import get_settings as _gs
            _base = str(_gs().FRONTEND_URL).rstrip("/") if hasattr(_gs(), "FRONTEND_URL") else "https://support.tibos.in"
            await send_csat_survey(full, db, _base)
        except Exception as _ce:
            logger.warning(f"[CSAT] Survey dispatch failed: {_ce}")

    # ── Email: status-change notifications to submitter ──────────────────
    if "status" in update_data and full.email:
        new_st = update_data["status"]
        email_cfg_data: dict | None = None
        _agent = full.assignee.name if full.assignee else None

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
                include_reopen=False,
            )
        elif new_st == TicketStatus.resolved:
            resolution_note = full.resolution or ""
            email_cfg_data = dict(
                subject=f"[{full.ticket_id}] Your ticket has been resolved",
                body=(
                    f"<p>Hi <strong>{full.submitter_name}</strong>,</p>"
                    f"<p>Your ticket <strong>{full.subject}</strong> has been <strong>resolved</strong>.</p>"
                    + (f"<p><strong>Resolution:</strong><br/>{resolution_note}</p>" if resolution_note else "")
                ),
                action_label="Ticket Resolved",
                action_color="#10b981",
                include_reopen=True,
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
                include_reopen=False,
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
                include_reopen=False,
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
                assignee_name=_agent,
                include_reopen=email_cfg_data.get("include_reopen", False),
            )
            db.add(TicketTimeline(
                ticket_id=full.id,
                type=TimelineType.email_out,
                text=f"Status update email sent to <strong>{full.email}</strong>",
                author_id=current_user.id,
            ))
            await db.flush()

    # Commit before broadcasting so any SSE-triggered fetchTickets sees the update
    await db.commit()
    try:
        await broadcast_ticket_event(
            "ticket_updated",
            {"ticket_id": str(ticket_id), "ticket_number": full.ticket_id},
            actor_user_id=str(current_user.id),
        )
    except Exception:
        pass  # broadcast failure must not undo the committed update

    _inject_empty_attachments(full)
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

    # ── First Response Time stamp ─────────────────────────────────────────────
    try:
        from app.services.frt_service import maybe_stamp_first_response
        await maybe_stamp_first_response(ticket, current_user.id, db)
    except Exception as _fe:
        logger.warning(f"[FRT] stamp failed: {_fe}")

    # ── Automation: comment_added trigger ─────────────────────────────────────
    try:
        from app.services.automation_engine import run_automation
        await run_automation("comment_added", ticket, db)
    except Exception as _ae:
        logger.warning(f"[automation] comment_added hook failed: {_ae}")

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
            assignee_name=current_user.name,
        )
        db.add(TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.email_out,
            text=f"Comment sent as email to <strong>{ticket.email}</strong> by <strong>{current_user.name}</strong>",
            author_id=current_user.id,
        ))
        await db.flush()

    full = await _get_ticket_or_404(ticket_id, db)

    await db.commit()
    try:
        await broadcast_ticket_event(
            "ticket_comment",
            {"ticket_id": str(ticket_id), "author": current_user.name},
            actor_user_id=str(current_user.id),
        )
    except Exception:
        pass

    _inject_empty_attachments(full)
    return TicketOut.model_validate(full)


@router.put("/{ticket_id}/tasks", response_model=TicketOut)
async def update_tasks(
    ticket_id: uuid.UUID,
    body: TicketDataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    ticket.tasks = body.items
    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()
    full = await _get_ticket_or_404(ticket_id, db)
    _inject_empty_attachments(full)
    return TicketOut.model_validate(full)


@router.put("/{ticket_id}/work-log", response_model=TicketOut)
async def update_work_log(
    ticket_id: uuid.UUID,
    body: TicketDataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    ticket.work_log = body.items
    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()
    full = await _get_ticket_or_404(ticket_id, db)
    _inject_empty_attachments(full)
    return TicketOut.model_validate(full)


@router.put("/{ticket_id}/reminders", response_model=TicketOut)
async def update_reminders(
    ticket_id: uuid.UUID,
    body: TicketDataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    ticket.reminders = body.items
    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()
    full = await _get_ticket_or_404(ticket_id, db)
    _inject_empty_attachments(full)
    return TicketOut.model_validate(full)


@router.put("/{ticket_id}/approvals", response_model=TicketOut)
async def update_approvals(
    ticket_id: uuid.UUID,
    body: TicketDataUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ticket = await _get_ticket_or_404(ticket_id, db)
    ticket.approvals = body.items
    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()
    full = await _get_ticket_or_404(ticket_id, db)
    _inject_empty_attachments(full)
    return TicketOut.model_validate(full)


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Soft-delete: marks ticket as deleted (is_deleted=True). Recoverable via /restore."""
    ticket = await _get_ticket_or_404(ticket_id, db)
    ticket.is_deleted = True
    ticket.deleted_at = datetime.now(timezone.utc)
    ticket.updated_at = datetime.now(timezone.utc)
    await db.commit()
    try:
        await broadcast_ticket_event("ticket_deleted", {"ticket_id": str(ticket_id)})
    except Exception:
        pass


@router.post("/{ticket_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Restore a soft-deleted ticket back to active."""
    ticket = await _get_any_ticket_or_404(ticket_id, db)
    ticket.is_deleted = False
    ticket.deleted_at = None
    ticket.updated_at = datetime.now(timezone.utc)
    await db.commit()
    try:
        await broadcast_ticket_event("ticket_restored", {"ticket_id": str(ticket_id)})
    except Exception:
        pass


@router.delete("/{ticket_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanent_delete_ticket(
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Hard-delete: permanently removes the ticket and all related data from the database."""
    await _get_any_ticket_or_404(ticket_id, db)
    await db.execute(delete(Ticket).where(Ticket.id == ticket_id))
    await db.commit()
    try:
        await broadcast_ticket_event("ticket_deleted", {"ticket_id": str(ticket_id)})
    except Exception:
        pass


@router.post("/bulk", status_code=status.HTTP_200_OK)
async def bulk_action(
    body: BulkTicketAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.action == "delete":
        await db.execute(
            update(Ticket)
            .where(Ticket.id.in_(body.ticket_ids))
            .values(is_deleted=True, deleted_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        )
    else:
        new_status = TicketStatus.resolved if body.action == "resolve" else TicketStatus.closed
        await db.execute(
            update(Ticket)
            .where(Ticket.id.in_(body.ticket_ids))
            .values(status=new_status, updated_at=datetime.now(timezone.utc))
        )

    await db.commit()
    try:
        await broadcast_ticket_event(
            "tickets_bulk_updated",
            {"action": body.action, "count": len(body.ticket_ids)},
            actor_user_id=str(current_user.id),
        )
    except Exception:
        pass
    return {"affected": len(body.ticket_ids)}
