"""
Agent-facing email inbox API (/email/*).

Implements the contract expected by the frontend Email page
(Tibos_TT_Frontend/src/api/emailApi.ts): accounts, threads, messages,
templates, signatures, routing rules, and AI assist endpoints.

All endpoints require an authenticated staff user (technician or admin);
account management is admin-only.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.email_inbox import (
    EmailAccount,
    EmailMessage,
    EmailRoutingRule,
    EmailSignature,
    EmailThread,
    InboxEmailTemplate,
)
from app.models.ticket import Ticket
from app.models.user import User, UserRole
from app.schemas.email_inbox import (
    AISuggestOut,
    AISuggestRequest,
    AISummarizeOut,
    AISummarizeRequest,
    EmailAccountCreate,
    EmailAccountOut,
    EmailAccountUpdate,
    EmailMessageOut,
    EmailRoutingRuleCreate,
    EmailRoutingRuleOut,
    EmailRoutingRuleUpdate,
    EmailSignatureCreate,
    EmailSignatureOut,
    EmailSignatureUpdate,
    EmailTemplateCreate,
    EmailTemplateOut,
    EmailTemplateUpdate,
    EmailThreadOut,
    EmailThreadUpdate,
    FetchResult,
    ForwardRequest,
    LinkTicketRequest,
    MarkReadRequest,
    PaginatedThreads,
    SendEmailRequest,
    TemplateRenderOut,
    TemplateRenderRequest,
)
from app.services import email_ai_service, email_inbox_service
from app.services.email_ai_service import AIUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/email", tags=["email-inbox"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def require_staff(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in (UserRole.admin, UserRole.technician):
        raise HTTPException(status_code=403, detail="Staff role required")
    return current_user


async def _get_account(db: AsyncSession, account_id: uuid.UUID) -> EmailAccount:
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Email account not found")
    return account


async def _get_thread(db: AsyncSession, thread_id: uuid.UUID) -> EmailThread:
    thread = await db.get(EmailThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


# ── Accounts ────────────────────────────────────────────────────────────────


@router.get("/accounts", response_model=list[EmailAccountOut])
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    res = await db.execute(select(EmailAccount).order_by(EmailAccount.created_at))
    return res.scalars().all()


@router.post("/accounts", response_model=EmailAccountOut, status_code=201)
async def create_account(
    payload: EmailAccountCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    account = EmailAccount(**payload.model_dump())
    # First account becomes the default automatically
    existing = (await db.execute(select(func.count()).select_from(EmailAccount))).scalar_one()
    if existing == 0:
        account.is_default = True
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.patch("/accounts/{account_id}", response_model=EmailAccountOut)
async def update_account(
    account_id: uuid.UUID,
    payload: EmailAccountUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    account = await _get_account(db, account_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_default"):
        # Only one default at a time
        res = await db.execute(select(EmailAccount).where(EmailAccount.is_default.is_(True)))
        for other in res.scalars().all():
            other.is_default = False
    for field, value in changes.items():
        setattr(account, field, value)
    account.updated_at = _utcnow()
    await db.commit()
    await db.refresh(account)
    return account


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    account = await _get_account(db, account_id)
    await db.delete(account)
    await db.commit()


@router.post("/accounts/{account_id}/fetch", response_model=FetchResult)
async def trigger_fetch(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    account = await _get_account(db, account_id)
    if not account.is_active:
        raise HTTPException(status_code=400, detail="Account is disabled")
    try:
        fetched = await email_inbox_service.fetch_account(db, account)
    except Exception as exc:
        logger.error(f"[email-inbox] fetch failed for {account.email_address}: {exc}")
        raise HTTPException(status_code=502, detail=f"Mail fetch failed: {exc}")
    return FetchResult(fetched=fetched)


# ── Threads ─────────────────────────────────────────────────────────────────


@router.get("/threads", response_model=PaginatedThreads)
async def list_threads(
    account_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
    is_read: bool | None = None,
    is_starred: bool | None = None,
    is_archived: bool | None = None,
    is_spam: bool | None = None,
    ticket_id: uuid.UUID | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)

    query = select(EmailThread)
    if account_id is not None:
        query = query.where(EmailThread.account_id == account_id)
    if is_read is not None:
        query = query.where(EmailThread.is_read.is_(is_read))
    if is_starred is not None:
        query = query.where(EmailThread.is_starred.is_(is_starred))
    if is_archived is not None:
        query = query.where(EmailThread.is_archived.is_(is_archived))
    if is_spam is not None:
        query = query.where(EmailThread.is_spam.is_(is_spam))
    if ticket_id is not None:
        query = query.where(EmailThread.ticket_id == ticket_id)
    if search:
        like = f"%{search.lower()}%"
        query = query.where(
            or_(
                func.lower(EmailThread.subject).like(like),
                func.lower(func.coalesce(EmailThread.snippet, "")).like(like),
            )
        )

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    res = await db.execute(
        query.order_by(EmailThread.last_message_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = res.scalars().all()
    pages = max(1, -(-total // page_size))
    return PaginatedThreads(
        items=[EmailThreadOut.model_validate(t) for t in items],
        total=total,
        page=page,
        pages=pages,
    )


@router.get("/threads/{thread_id}", response_model=EmailThreadOut)
async def get_thread(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    return await _get_thread(db, thread_id)


@router.get("/threads/{thread_id}/messages", response_model=list[EmailMessageOut])
async def get_thread_messages(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    await _get_thread(db, thread_id)
    res = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.thread_id == thread_id)
        .order_by(EmailMessage.received_at)
    )
    return res.scalars().all()


@router.patch("/threads/{thread_id}", response_model=EmailThreadOut)
async def update_thread(
    thread_id: uuid.UUID,
    payload: EmailThreadUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    thread = await _get_thread(db, thread_id)
    changes = payload.model_dump(exclude_unset=True)
    if "ticket_id" in changes and changes["ticket_id"] is not None:
        if not await db.get(Ticket, changes["ticket_id"]):
            raise HTTPException(status_code=404, detail="Ticket not found")
    if changes.pop("is_read", None) is not None:
        # Marking the whole thread read/unread cascades to inbound messages
        is_read = payload.is_read
        res = await db.execute(select(EmailMessage).where(EmailMessage.thread_id == thread_id))
        for m in res.scalars().all():
            if m.direction == "inbound":
                m.is_read = is_read
                m.read_at = _utcnow() if is_read else None
    for field, value in changes.items():
        setattr(thread, field, value)
    await email_inbox_service.refresh_thread_counters(db, thread)
    if payload.is_read is not None:
        thread.is_read = payload.is_read
    await db.commit()
    await db.refresh(thread)
    return thread


@router.post("/threads/{thread_id}/link-ticket", response_model=EmailThreadOut)
async def link_ticket(
    thread_id: uuid.UUID,
    payload: LinkTicketRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    thread = await _get_thread(db, thread_id)
    if payload.ticket_id is not None:
        if not await db.get(Ticket, payload.ticket_id):
            raise HTTPException(status_code=404, detail="Ticket not found")
    thread.ticket_id = payload.ticket_id
    thread.updated_at = _utcnow()
    await db.commit()
    await db.refresh(thread)
    return thread


@router.post("/threads/{thread_id}/mark-read", status_code=204)
async def mark_read(
    thread_id: uuid.UUID,
    payload: MarkReadRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    thread = await _get_thread(db, thread_id)
    query = select(EmailMessage).where(EmailMessage.thread_id == thread_id)
    if payload.message_ids:
        query = query.where(EmailMessage.id.in_(payload.message_ids))
    res = await db.execute(query)
    for m in res.scalars().all():
        m.is_read = payload.is_read
        m.read_at = _utcnow() if payload.is_read else None
    await email_inbox_service.refresh_thread_counters(db, thread)
    await db.commit()


# ── Messages ────────────────────────────────────────────────────────────────


@router.post("/messages/send", response_model=EmailMessageOut)
async def send_message(
    payload: SendEmailRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    account = await _get_account(db, payload.account_id)
    if not account.is_active:
        raise HTTPException(status_code=400, detail="Account is disabled")

    thread = None
    if payload.thread_id is not None:
        thread = await _get_thread(db, payload.thread_id)

    in_reply_to = None
    if payload.in_reply_to_message_id is not None:
        in_reply_to = await db.get(EmailMessage, payload.in_reply_to_message_id)

    body_html = payload.body_html
    if payload.template_id is not None:
        template = await db.get(InboxEmailTemplate, payload.template_id)
        if template:
            template.use_count += 1

    if payload.signature_id is not None:
        signature = await db.get(EmailSignature, payload.signature_id)
        if signature and signature.body_html:
            body_html = f"{body_html}<br/><br/>{signature.body_html}"

    return await email_inbox_service.send_message(
        db,
        account,
        thread=thread,
        to=[r.model_dump(mode="json") for r in payload.to],
        cc=[r.model_dump(mode="json") for r in payload.cc],
        bcc=[r.model_dump(mode="json") for r in payload.bcc],
        subject=payload.subject,
        body_html=body_html,
        body_text=payload.body_text,
        message_type=payload.message_type,
        in_reply_to_message=in_reply_to,
        agent_id=current_user.id,
    )


@router.post("/messages/{message_id}/forward", response_model=EmailMessageOut)
async def forward_message(
    message_id: uuid.UUID,
    payload: ForwardRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    original = await db.get(EmailMessage, message_id)
    if not original:
        raise HTTPException(status_code=404, detail="Message not found")
    account = await _get_account(db, original.account_id)
    thread = await db.get(EmailThread, original.thread_id)

    note = f"<p>{payload.additional_note}</p><hr/>" if payload.additional_note else ""
    original_body = original.body_html or f"<pre>{original.body_text or ''}</pre>"
    fwd_html = (
        f"{note}<p>---------- Forwarded message ----------<br/>"
        f"From: {original.from_name or ''} &lt;{original.from_email}&gt;<br/>"
        f"Subject: {original.subject or ''}</p>{original_body}"
    )
    subject = original.subject or ""
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"

    return await email_inbox_service.send_message(
        db,
        account,
        thread=thread,
        to=[r.model_dump(mode="json") for r in payload.to],
        cc=[r.model_dump(mode="json") for r in payload.cc],
        bcc=[],
        subject=subject,
        body_html=fwd_html,
        body_text=None,
        message_type="forward",
        in_reply_to_message=None,
        agent_id=current_user.id,
    )


@router.get("/messages/{message_id}", response_model=EmailMessageOut)
async def get_message(
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    message = await db.get(EmailMessage, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


# ── Templates ───────────────────────────────────────────────────────────────


@router.get("/templates", response_model=list[EmailTemplateOut])
async def list_templates(
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    query = select(InboxEmailTemplate).order_by(InboxEmailTemplate.name)
    if category:
        query = query.where(InboxEmailTemplate.category == category)
    res = await db.execute(query)
    return res.scalars().all()


@router.post("/templates", response_model=EmailTemplateOut, status_code=201)
async def create_template(
    payload: EmailTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    template = InboxEmailTemplate(
        **payload.model_dump(exclude={"variables"}),
        variables=[v.model_dump(mode="json") for v in payload.variables],
        created_by_id=current_user.id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.patch("/templates/{template_id}", response_model=EmailTemplateOut)
async def update_template(
    template_id: uuid.UUID,
    payload: EmailTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    template = await db.get(InboxEmailTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    changes = payload.model_dump(exclude_unset=True)
    if "variables" in changes and changes["variables"] is not None:
        changes["variables"] = [
            v.model_dump(mode="json") for v in payload.variables
        ]
    for field, value in changes.items():
        setattr(template, field, value)
    template.updated_at = _utcnow()
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    template = await db.get(InboxEmailTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(template)
    await db.commit()


@router.post("/templates/render", response_model=TemplateRenderOut)
async def render_template(
    payload: TemplateRenderRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    template = await db.get(InboxEmailTemplate, payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    variables = dict(payload.variables)
    # Fall back to declared defaults for missing variables
    for v in template.variables or []:
        name = v.get("name")
        if name and name not in variables and v.get("default") is not None:
            variables[name] = v["default"]
    render = email_inbox_service.render_template_string
    return TemplateRenderOut(
        subject=render(template.subject, variables),
        body_html=render(template.body_html, variables),
        body_text=render(template.body_text, variables) if template.body_text else None,
    )


# ── Signatures ──────────────────────────────────────────────────────────────


@router.get("/signatures", response_model=list[EmailSignatureOut])
async def list_signatures(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    res = await db.execute(
        select(EmailSignature)
        .where(EmailSignature.agent_id == current_user.id)
        .order_by(EmailSignature.created_at)
    )
    return res.scalars().all()


@router.post("/signatures", response_model=EmailSignatureOut, status_code=201)
async def create_signature(
    payload: EmailSignatureCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if payload.is_default:
        res = await db.execute(
            select(EmailSignature).where(EmailSignature.agent_id == current_user.id)
        )
        for other in res.scalars().all():
            other.is_default = False
    signature = EmailSignature(**payload.model_dump(), agent_id=current_user.id)
    db.add(signature)
    await db.commit()
    await db.refresh(signature)
    return signature


@router.patch("/signatures/{signature_id}", response_model=EmailSignatureOut)
async def update_signature(
    signature_id: uuid.UUID,
    payload: EmailSignatureUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    signature = await db.get(EmailSignature, signature_id)
    if not signature or signature.agent_id != current_user.id:
        raise HTTPException(status_code=404, detail="Signature not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_default"):
        res = await db.execute(
            select(EmailSignature).where(EmailSignature.agent_id == current_user.id)
        )
        for other in res.scalars().all():
            other.is_default = False
    for field, value in changes.items():
        setattr(signature, field, value)
    signature.updated_at = _utcnow()
    await db.commit()
    await db.refresh(signature)
    return signature


@router.delete("/signatures/{signature_id}", status_code=204)
async def delete_signature(
    signature_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    signature = await db.get(EmailSignature, signature_id)
    if not signature or signature.agent_id != current_user.id:
        raise HTTPException(status_code=404, detail="Signature not found")
    await db.delete(signature)
    await db.commit()


# ── Routing rules ───────────────────────────────────────────────────────────


@router.get("/routing-rules", response_model=list[EmailRoutingRuleOut])
async def list_routing_rules(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    res = await db.execute(
        select(EmailRoutingRule).order_by(EmailRoutingRule.priority.desc())
    )
    return res.scalars().all()


@router.post("/routing-rules", response_model=EmailRoutingRuleOut, status_code=201)
async def create_routing_rule(
    payload: EmailRoutingRuleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    if payload.account_id is not None:
        await _get_account(db, payload.account_id)
    rule = EmailRoutingRule(
        **payload.model_dump(exclude={"conditions", "actions"}),
        conditions=[c.model_dump(mode="json") for c in payload.conditions],
        actions=[a.model_dump(mode="json") for a in payload.actions],
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/routing-rules/{rule_id}", response_model=EmailRoutingRuleOut)
async def update_routing_rule(
    rule_id: uuid.UUID,
    payload: EmailRoutingRuleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    rule = await db.get(EmailRoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("conditions") is not None:
        changes["conditions"] = [c.model_dump(mode="json") for c in payload.conditions]
    if changes.get("actions") is not None:
        changes["actions"] = [a.model_dump(mode="json") for a in payload.actions]
    for field, value in changes.items():
        setattr(rule, field, value)
    rule.updated_at = _utcnow()
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/routing-rules/{rule_id}", status_code=204)
async def delete_routing_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    rule = await db.get(EmailRoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    await db.delete(rule)
    await db.commit()


# ── AI ──────────────────────────────────────────────────────────────────────


@router.post("/ai/suggest-reply", response_model=AISuggestOut)
async def ai_suggest_reply(
    payload: AISuggestRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    message = await db.get(EmailMessage, payload.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    res = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.thread_id == message.thread_id)
        .where(EmailMessage.id != message.id)
        .order_by(EmailMessage.received_at)
    )
    context = [
        {
            "direction": m.direction,
            "from_name": m.from_name,
            "from_email": m.from_email,
            "body": m.body_stripped or m.body_text or "",
        }
        for m in res.scalars().all()
    ]

    try:
        suggestion = await email_ai_service.suggest_reply(
            message_subject=message.subject or "",
            message_body=message.body_stripped or message.body_text or message.body_html or "",
            from_name=message.from_name or message.from_email,
            thread_context=context,
            tone=payload.tone,
            language=payload.language,
            max_length=payload.max_length,
        )
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error(f"[email-ai] suggest-reply failed: {exc}")
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}")

    message.ai_suggested_reply = suggestion
    await db.commit()
    return AISuggestOut(suggestion=suggestion, tone=payload.tone)


@router.post("/ai/summarize", response_model=AISummarizeOut)
async def ai_summarize(
    payload: AISummarizeRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    thread = await _get_thread(db, payload.thread_id)
    res = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.thread_id == thread.id)
        .order_by(EmailMessage.received_at)
    )
    messages = [
        {
            "direction": m.direction,
            "from_name": m.from_name,
            "from_email": m.from_email,
            "body": m.body_stripped or m.body_text or "",
        }
        for m in res.scalars().all()
    ]
    if not messages:
        raise HTTPException(status_code=400, detail="Thread has no messages to summarize")

    try:
        result = await email_ai_service.summarize_thread(
            subject=thread.subject,
            messages=messages,
            max_length=payload.max_length,
        )
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error(f"[email-ai] summarize failed: {exc}")
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}")

    return AISummarizeOut(**result)
