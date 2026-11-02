"""
Service layer for the agent-facing email inbox (/email API).

Fetching:  IMAP (aioimaplib) or Microsoft Graph per EmailAccount.
Sending:   SMTP (smtplib in a thread) or Graph sendMail.
Threading: inbound messages are grouped by In-Reply-To / References
           against stored RFC Message-IDs, falling back to normalised
           subject + account.

Attachments are stored as metadata only (filename/type/size) — the inbox
UI lists them but has no download endpoint yet.
"""
import asyncio
import logging
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_inbox import (
    EmailAccount,
    EmailMessage,
    EmailRoutingRule,
    EmailThread,
)
from app.services.email_parser import parse_raw_email

logger = logging.getLogger(__name__)

_SUBJECT_PREFIX_RE = re.compile(r"^\s*((re|fwd?|aw|sv)\s*:\s*)+", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_subject(subject: str) -> str:
    return _SUBJECT_PREFIX_RE.sub("", subject or "").strip().lower()


def render_template_string(text: str, variables: dict[str, str]) -> str:
    """Replace {{var}} placeholders; unknown placeholders are left intact."""
    def _sub(m: re.Match) -> str:
        key = m.group(1).strip()
        return variables.get(key, m.group(0))
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", _sub, text or "")


def _snippet(text: str | None, limit: int = 200) -> str | None:
    if not text:
        return None
    clean = re.sub(r"\s+", " ", text).strip()
    return clean[:limit] or None


# ── Thread helpers ──────────────────────────────────────────────────────────────

async def _find_thread_for_inbound(
    db: AsyncSession,
    account: EmailAccount,
    in_reply_to: str,
    references: str,
    subject: str,
) -> EmailThread | None:
    # 1. Match by RFC message ids in In-Reply-To / References
    ref_ids = [r for r in (references or "").split() if r] + ([in_reply_to] if in_reply_to else [])
    if ref_ids:
        res = await db.execute(
            select(EmailMessage.thread_id)
            .where(EmailMessage.account_id == account.id)
            .where(EmailMessage.rfc_message_id.in_(ref_ids))
            .limit(1)
        )
        thread_id = res.scalar_one_or_none()
        if thread_id:
            return await db.get(EmailThread, thread_id)

    # 2. Fall back to normalised subject on the same account
    norm = normalize_subject(subject)
    if norm:
        res = await db.execute(
            select(EmailThread)
            .where(EmailThread.account_id == account.id)
            .order_by(EmailThread.last_message_at.desc())
            .limit(50)
        )
        for t in res.scalars().all():
            if normalize_subject(t.subject) == norm:
                return t
    return None


def _recount_participants(thread: EmailThread, *emails: str) -> None:
    existing = set(thread.participant_emails or [])
    for e in emails:
        if e:
            existing.add(e.lower())
    thread.participant_emails = sorted(existing)


async def refresh_thread_counters(db: AsyncSession, thread: EmailThread) -> None:
    res = await db.execute(select(EmailMessage).where(EmailMessage.thread_id == thread.id))
    msgs = res.scalars().all()
    thread.message_count = len(msgs)
    thread.unread_count = sum(1 for m in msgs if not m.is_read and m.direction == "inbound")
    thread.is_read = thread.unread_count == 0
    thread.has_attachments = any(m.attachments for m in msgs)
    if msgs:
        thread.last_message_at = max(m.received_at for m in msgs)
        latest = max(msgs, key=lambda m: m.received_at)
        thread.snippet = _snippet(latest.body_stripped or latest.body_text)
    thread.updated_at = _utcnow()


# ── Routing rules ───────────────────────────────────────────────────────────────

_FIELD_GETTERS = {
    "from": lambda m: m.from_email or "",
    "from_email": lambda m: m.from_email or "",
    "subject": lambda m: m.subject or "",
    "body": lambda m: m.body_text or m.body_stripped or "",
    "to": lambda m: " ".join(r.get("email", "") for r in (m.to_recipients or [])),
}


def _condition_matches(cond: dict, msg: EmailMessage) -> bool:
    value = (_FIELD_GETTERS.get(cond.get("field", ""), lambda m: "")(msg)).lower()
    target = (cond.get("value") or "").lower()
    op = cond.get("operator", "contains")
    if op == "contains":
        return target in value
    if op == "not_contains":
        return target not in value
    if op == "equals":
        return value == target
    if op == "starts_with":
        return value.startswith(target)
    if op == "ends_with":
        return value.endswith(target)
    return False


async def apply_routing_rules(
    db: AsyncSession, account: EmailAccount, thread: EmailThread, msg: EmailMessage
) -> None:
    res = await db.execute(
        select(EmailRoutingRule)
        .where(EmailRoutingRule.is_active.is_(True))
        .where(
            (EmailRoutingRule.account_id == account.id)
            | (EmailRoutingRule.account_id.is_(None))
        )
        .order_by(EmailRoutingRule.priority.desc())
    )
    for rule in res.scalars().all():
        conditions = rule.conditions or []
        if not conditions:
            continue
        results = [_condition_matches(c, msg) for c in conditions]
        matched = all(results) if (rule.condition_logic or "AND") == "AND" else any(results)
        if not matched:
            continue
        for action in rule.actions or []:
            a_type = action.get("type")
            if a_type == "mark_spam":
                thread.is_spam = True
            elif a_type == "archive":
                thread.is_archived = True
            elif a_type == "star":
                thread.is_starred = True
            elif a_type == "mark_read":
                msg.is_read = True
                msg.read_at = _utcnow()


# ── Inbound storage ─────────────────────────────────────────────────────────────

async def store_inbound_email(
    db: AsyncSession, account: EmailAccount, raw: bytes
) -> EmailMessage | None:
    """Parse + persist one raw inbound email. Returns None if duplicate."""
    parsed = parse_raw_email(raw)

    # Dedupe on RFC Message-ID per account
    res = await db.execute(
        select(EmailMessage.id)
        .where(EmailMessage.account_id == account.id)
        .where(EmailMessage.rfc_message_id == parsed["message_id"])
        .limit(1)
    )
    if res.scalar_one_or_none():
        return None

    thread = await _find_thread_for_inbound(
        db, account, parsed["in_reply_to"], parsed["references"], parsed["subject"]
    )
    if thread is None:
        thread = EmailThread(
            account_id=account.id,
            subject=parsed["subject"][:500],
        )
        db.add(thread)
        await db.flush()

    msg = EmailMessage(
        thread_id=thread.id,
        account_id=account.id,
        rfc_message_id=parsed["message_id"],
        in_reply_to=parsed["in_reply_to"][:500] if parsed["in_reply_to"] else None,
        direction="inbound",
        message_type="reply" if parsed["in_reply_to"] else "original",
        from_email=parsed["from_email"][:255],
        from_name=(parsed["from_name"] or None),
        to_recipients=[{"email": account.email_address}],
        subject=parsed["subject"][:500],
        body_text=parsed["body"],
        body_stripped=parsed["body"],
        delivery_status="delivered",
        received_at=parsed["received_at"] or _utcnow(),
        attachments=[
            {
                "id": str(uuid.uuid4()),
                "filename": a["filename"],
                "content_type": a["content_type"],
                "size_bytes": len(a["content"]),
                "is_inline": False,
            }
            for a in parsed["attachments"]
        ],
    )
    db.add(msg)
    await db.flush()

    _recount_participants(thread, parsed["from_email"], account.email_address)
    await apply_routing_rules(db, account, thread, msg)
    await refresh_thread_counters(db, thread)
    return msg


# ── IMAP fetch ──────────────────────────────────────────────────────────────────

async def _fetch_imap_raw(account: EmailAccount, limit: int = 50) -> list[bytes]:
    import aioimaplib

    if account.imap_use_ssl:
        client = aioimaplib.IMAP4_SSL(host=account.imap_host, port=account.imap_port, timeout=30)
    else:
        client = aioimaplib.IMAP4(host=account.imap_host, port=account.imap_port, timeout=30)

    await client.wait_hello_from_server()
    login = await client.login(account.imap_username or account.email_address, account.imap_password or "")
    if login.result != "OK":
        raise RuntimeError(f"IMAP login failed: {login.result}")
    await client.select("INBOX")

    search = await client.search("UNSEEN")
    raws: list[bytes] = []
    if search.result == "OK" and search.lines:
        ids = search.lines[0].split() if search.lines[0] else []
        for msg_id in ids[:limit]:
            msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
            fetch = await client.fetch(msg_id_str, "(RFC822)")
            if fetch.result == "OK":
                for line in fetch.lines:
                    if isinstance(line, (bytes, bytearray)) and len(line) > 100:
                        raws.append(bytes(line))
                        break
                await client.store(msg_id_str, "+FLAGS", "\\Seen")
    await client.logout()
    return raws


# ── Microsoft Graph fetch ──────────────────────────────────────────────────────

async def _graph_token(account: EmailAccount) -> str:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{account.graph_tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": account.graph_client_id,
                "client_secret": account.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _fetch_graph(db: AsyncSession, account: EmailAccount, limit: int = 50) -> int:
    token = await _graph_token(account)
    mailbox = account.graph_user_id or account.email_address
    headers = {"Authorization": f"Bearer {token}"}
    stored = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/inbox/messages",
            params={"$filter": "isRead eq false", "$top": str(limit), "$orderby": "receivedDateTime asc"},
            headers=headers,
        )
        resp.raise_for_status()
        for m in resp.json().get("value", []):
            # Fetch full MIME so the shared parser handles it uniformly
            mime = await client.get(
                f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{m['id']}/$value",
                headers=headers,
            )
            if mime.status_code == 200:
                msg = await store_inbound_email(db, account, mime.content)
                if msg:
                    stored += 1
                await client.patch(
                    f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{m['id']}",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"isRead": True},
                )
    return stored


# ── Public fetch entry point ───────────────────────────────────────────────────

async def fetch_account(db: AsyncSession, account: EmailAccount) -> int:
    """Pull new mail for one account. Returns the number of stored messages."""
    stored = 0
    if account.protocol == "graph_api":
        stored = await _fetch_graph(db, account)
    else:
        if not account.imap_host:
            raise ValueError("IMAP host is not configured for this account")
        raws = await _fetch_imap_raw(account)
        for raw in raws:
            msg = await store_inbound_email(db, account, raw)
            if msg:
                stored += 1
    account.last_fetched_at = _utcnow()
    await db.commit()
    return stored


# ── Sending ────────────────────────────────────────────────────────────────────

def _build_mime(
    account: EmailAccount,
    to: list[dict],
    cc: list[dict],
    bcc: list[dict],
    subject: str,
    body_html: str,
    body_text: str | None,
    in_reply_to_rfc_id: str | None,
) -> tuple[MIMEMultipart, str, list[str]]:
    msg = MIMEMultipart("alternative")
    rfc_id = make_msgid()
    msg["Message-ID"] = rfc_id
    msg["Subject"] = subject
    msg["From"] = formataddr((account.display_name or account.name, account.email_address))
    msg["To"] = ", ".join(formataddr((r.get("name") or "", r["email"])) for r in to)
    if cc:
        msg["Cc"] = ", ".join(formataddr((r.get("name") or "", r["email"])) for r in cc)
    msg["Date"] = formatdate(localtime=True)
    if in_reply_to_rfc_id:
        msg["In-Reply-To"] = in_reply_to_rfc_id
        msg["References"] = in_reply_to_rfc_id
    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    all_rcpt = [r["email"] for r in to + cc + bcc]
    return msg, rfc_id, all_rcpt


def _smtp_send_sync(account: EmailAccount, msg: MIMEMultipart, recipients: list[str]) -> None:
    host = account.smtp_host
    port = account.smtp_port
    if not host:
        raise ValueError("SMTP host is not configured for this account")
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if account.smtp_use_tls:
            server.starttls(context=ssl.create_default_context())
    try:
        if account.smtp_username and account.smtp_password:
            server.login(account.smtp_username, account.smtp_password)
        server.sendmail(account.email_address, recipients, msg.as_string())
    finally:
        server.quit()


async def _graph_send(account: EmailAccount, to: list[dict], cc: list[dict], bcc: list[dict],
                      subject: str, body_html: str) -> None:
    token = await _graph_token(account)
    mailbox = account.graph_user_id or account.email_address

    def _rcpt(r: dict) -> dict:
        return {"emailAddress": {"address": r["email"], **({"name": r["name"]} if r.get("name") else {})}}

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [_rcpt(r) for r in to],
            "ccRecipients": [_rcpt(r) for r in cc],
            "bccRecipients": [_rcpt(r) for r in bcc],
        },
        "saveToSentItems": True,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()


async def send_message(
    db: AsyncSession,
    account: EmailAccount,
    *,
    thread: EmailThread | None,
    to: list[dict],
    cc: list[dict],
    bcc: list[dict],
    subject: str,
    body_html: str,
    body_text: str | None,
    message_type: str,
    in_reply_to_message: EmailMessage | None,
    agent_id: uuid.UUID,
) -> EmailMessage:
    """Send an email and persist the outbound message (creating a thread if needed)."""
    if thread is None:
        thread = EmailThread(account_id=account.id, subject=subject[:500])
        db.add(thread)
        await db.flush()

    out = EmailMessage(
        thread_id=thread.id,
        account_id=account.id,
        direction="outbound",
        message_type=message_type,
        from_email=account.email_address,
        from_name=account.display_name or account.name,
        sent_by_agent_id=agent_id,
        to_recipients=to,
        cc_recipients=cc,
        bcc_recipients=bcc,
        subject=subject[:500],
        body_html=body_html,
        body_text=body_text,
        body_stripped=body_text or _snippet(re.sub(r"<[^>]+>", " ", body_html), 100000),
        delivery_status="pending",
        is_read=True,
        received_at=_utcnow(),
    )
    db.add(out)
    await db.flush()

    # Internal notes are never actually sent
    if message_type == "internal_note":
        out.delivery_status = "delivered"
        out.sent_at = _utcnow()
    else:
        reply_rfc_id = in_reply_to_message.rfc_message_id if in_reply_to_message else None
        try:
            if account.protocol == "graph_api":
                await _graph_send(account, to, cc, bcc, subject, body_html)
                out.rfc_message_id = make_msgid()
            else:
                mime, rfc_id, recipients = _build_mime(
                    account, to, cc, bcc, subject, body_html, body_text, reply_rfc_id
                )
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _smtp_send_sync, account, mime, recipients)
                out.rfc_message_id = rfc_id
            out.delivery_status = "sent"
            out.sent_at = _utcnow()
        except Exception as exc:
            logger.error(f"[email-inbox] send failed via {account.protocol}: {exc}")
            out.delivery_status = "failed"
            out.delivery_error = str(exc)[:2000]

    _recount_participants(thread, *[r["email"] for r in to])
    await refresh_thread_counters(db, thread)
    await db.commit()
    await db.refresh(out)
    return out
