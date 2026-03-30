"""
Email-to-Ticket background poller.

Supported auth modes
────────────────────
• basic  → IMAP LOGIN with username + password
• oauth  → IMAP AUTHENTICATE XOAUTH2 using the stored OAuth access token
           (Gmail IMAP; token auto-refreshed when within 5 min of expiry)
• graph  → Microsoft Graph API  (no IMAP needed; reads messages via REST)
           Uses the same OAuth refresh token stored in EmailConfig.

Flow (IMAP)
───────────
1.  Load InboundEmailConfig from DB.
2.  Connect + authenticate.
3.  SELECT folder.
4.  SEARCH UNSEEN.
5.  For each UID:
      a. FETCH RFC822.
      b. parse_raw_email() → ParsedEmail.
      c. De-duplicate on Message-ID (EmailTicketLog).
      d. Create Ticket + TicketTimeline row.
      e. Create EmailTicketLog row.
      f. Notify via SSE / WS.
      g. STORE +FLAGS (\Seen) — or move to sub-folder.
6.  Update last_polled_at + processed_count.
7.  Sleep poll_interval_minutes.

Flow (Graph)
────────────
Same as above but steps 2-4 use httpx to call
  GET /users/{mailbox}/mailFolders/inbox/messages?$filter=isRead eq false
and step 5g calls PATCH /messages/{id} {"isRead": true}.
"""

import asyncio
import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import aioimaplib
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.admin import EmailConfig
from app.models.inbound_email import (
    EmailLogStatus,
    EmailTicketLog,
    InboundAuthType,
    InboundEmailConfig,
)
from app.models.ticket import Ticket, TicketTimeline, TimelineType
from app.services.email_parser import parse_raw_email
from app.services.notification_service import broadcast_ticket_event
from app.services.cache_service import invalidate_tickets
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _build_xoauth2_string(user: str, access_token: str) -> str:
    """Build the SASL XOAUTH2 base64 string for IMAP AUTHENTICATE."""
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode()).decode()


async def _refresh_google_token(cfg: EmailConfig, db: AsyncSession) -> str:
    """
    Refresh a Google OAuth access token using the stored refresh token.
    Updates EmailConfig in place. Returns the new access token.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": cfg.oauth_refresh_token,
                "client_id":     cfg.oauth_client_id,
                "client_secret": cfg.oauth_client_secret,
            },
            timeout=15,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: {resp.text}")

    data = resp.json()
    cfg.oauth_access_token = data["access_token"]
    cfg.oauth_token_expiry = datetime.now(timezone.utc) + timedelta(
        seconds=data.get("expires_in", 3600)
    )
    await db.flush()
    return cfg.oauth_access_token


async def _ensure_valid_token(cfg: EmailConfig, db: AsyncSession) -> str:
    """Return a valid (non-expired) access token, refreshing if needed."""
    if cfg.oauth_token_expiry:
        remaining = cfg.oauth_token_expiry - datetime.now(timezone.utc)
        if remaining.total_seconds() < 300:  # refresh within 5 min of expiry
            return await _refresh_google_token(cfg, db)
    if not cfg.oauth_access_token:
        raise RuntimeError("No OAuth access token stored. Re-authorize first.")
    return cfg.oauth_access_token


# ── Graph API helpers ─────────────────────────────────────────────────────────

async def _graph_get_unread(
    mailbox: str, token: str
) -> list[dict]:
    url = (
        f"https://graph.microsoft.com/v1.0/users/{mailbox}"
        "/mailFolders/inbox/messages"
        "?$filter=isRead eq false"
        "&$select=id,subject,from,body,receivedDateTime,internetMessageId"
        "&$top=50"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Graph API error {resp.status_code}: {resp.text}")
    return resp.json().get("value", [])


async def _graph_mark_read(mailbox: str, token: str, msg_id: str) -> None:
    url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{msg_id}"
    async with httpx.AsyncClient() as client:
        await client.patch(
            url,
            json={"isRead": True},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            timeout=10,
        )


# ── Ticket creation helper ────────────────────────────────────────────────────

async def _create_ticket_from_email(
    db: AsyncSession,
    inbound: InboundEmailConfig,
    message_id: str,
    from_email: str,
    from_name: str,
    subject: str,
    body: str,
    received_at: Optional[datetime],
) -> tuple[Ticket, EmailTicketLog]:
    """
    Create a Ticket + EmailTicketLog row for one incoming email.
    Raises ValueError if message_id already processed (duplicate).
    """
    # De-duplicate check
    dup = await db.execute(
        select(EmailTicketLog).where(EmailTicketLog.message_id == message_id)
    )
    if dup.scalar_one_or_none():
        raise ValueError(f"Duplicate message_id: {message_id}")

    # Derive company from email domain  e.g. alice@acme.com → acme.com
    company = from_email.split("@")[1].lower() if "@" in from_email else ""

    # Use the configured default category; fall back to built-in "email" slug
    category_slug = inbound.default_category or "email"

    ticket = Ticket(
        subject=subject or "(no subject)",
        category=category_slug,
        priority=inbound.default_priority,
        submitter_name=from_name or from_email,  # who submitted (sender)
        company=company,                          # derived from email domain
        contact_name=from_name or from_email,     # sender name as contact
        email=from_email,                         # sender email as contact email
        description=body or "(empty body)",
        assignee_id=inbound.default_assignee_id,
        created_at=received_at or datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(ticket)
    await db.flush()

    # Timeline: auto-created via email
    entry = TicketTimeline(
        ticket_id=ticket.id,
        type=TimelineType.created,
        text=(
            f"Ticket auto-created from email by "
            f"<strong>{from_name or from_email}</strong> "
            f"&lt;{from_email}&gt;"
        ),
    )
    db.add(entry)

    if inbound.default_assignee_id:
        db.add(TicketTimeline(
            ticket_id=ticket.id,
            type=TimelineType.assign,
            text="Auto-assigned by inbound email rule",
        ))

    log = EmailTicketLog(
        inbound_config_id=inbound.id,
        message_id=message_id,
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        received_at=received_at,
        status=EmailLogStatus.processed,
        ticket_id=ticket.id,
    )
    db.add(log)
    await db.flush()
    return ticket, log


# ── IMAP poller ───────────────────────────────────────────────────────────────

async def _poll_imap(inbound: InboundEmailConfig, email_cfg: EmailConfig) -> int:
    """Connect to IMAP, fetch UNSEEN messages, create tickets. Returns count."""
    processed = 0

    # Determine credentials
    user = inbound.imap_user or ""
    auth_type = inbound.auth_type

    if inbound.imap_ssl:
        client = aioimaplib.IMAP4_SSL(
            host=inbound.imap_host or "localhost",
            port=inbound.imap_port,
        )
    else:
        client = aioimaplib.IMAP4(
            host=inbound.imap_host or "localhost",
            port=inbound.imap_port,
        )

    await client.wait_hello_from_server()

    try:
        # Authenticate
        if auth_type == InboundAuthType.oauth:
            async with AsyncSessionLocal() as tmp_db:
                access_token = await _ensure_valid_token(email_cfg, tmp_db)
                await tmp_db.commit()
            xoauth2 = _build_xoauth2_string(user, access_token)
            res = await client.authenticate("XOAUTH2", lambda _: xoauth2.encode())
            if res[0] != "OK":
                raise RuntimeError(f"XOAUTH2 auth failed: {res}")
        else:
            res = await client.login(user, inbound.imap_pass or "")
            if res[0] != "OK":
                raise RuntimeError(f"IMAP LOGIN failed: {res}")

        # Select folder
        res = await client.select(inbound.imap_folder)
        if res[0] != "OK":
            raise RuntimeError(f"SELECT {inbound.imap_folder} failed: {res}")

        # Search for unseen messages
        res = await client.search("UNSEEN")
        if res[0] != "OK":
            return 0
        uid_list = res[1][0].decode().split()
        if not uid_list or uid_list == [""]:
            return 0

        logger.info(f"Email poller: found {len(uid_list)} unseen message(s)")

        for uid in uid_list:
            try:
                # Fetch raw RFC822 bytes
                fetch_res = await client.fetch(uid, "(RFC822)")
                if fetch_res[0] != "OK":
                    continue

                # aioimaplib returns lines; find the bytes line
                raw: bytes | None = None
                for part in fetch_res[1]:
                    if isinstance(part, bytes) and len(part) > 100:
                        raw = part
                        break
                if not raw:
                    continue

                parsed = parse_raw_email(raw)

                async with AsyncSessionLocal() as db:
                    try:
                        ticket, log = await _create_ticket_from_email(
                            db, inbound,
                            message_id=parsed["message_id"],
                            from_email=parsed["from_email"],
                            from_name=parsed["from_name"],
                            subject=parsed["subject"],
                            body=parsed["body"],
                            received_at=parsed["received_at"],
                        )
                        await db.commit()
                        processed += 1

                        # Invalidate cache + broadcast
                        redis = await get_redis()
                        await invalidate_tickets(redis)
                        await broadcast_ticket_event(
                            "ticket_created",
                            {
                                "ticket_id": str(ticket.id),
                                "ticket_number": ticket.ticket_id,
                                "source": "email",
                                "from_email": parsed["from_email"],
                            },
                        )
                        logger.info(
                            f"Email→Ticket: {ticket.ticket_id} from <{parsed['from_email']}> "
                            f"subject='{parsed['subject'][:60]}'"
                        )

                    except ValueError:
                        # Duplicate – mark seen silently
                        async with AsyncSessionLocal() as dup_db:
                            dup_log = EmailTicketLog(
                                inbound_config_id=inbound.id,
                                message_id=parsed["message_id"],
                                from_email=parsed["from_email"],
                                from_name=parsed["from_name"],
                                subject=parsed["subject"],
                                received_at=parsed["received_at"],
                                status=EmailLogStatus.duplicate,
                            )
                            dup_db.add(dup_log)
                            await dup_db.commit()

                    except Exception as e:
                        logger.error(f"Error creating ticket from email {uid}: {e}")
                        async with AsyncSessionLocal() as err_db:
                            err_log = EmailTicketLog(
                                inbound_config_id=inbound.id,
                                message_id=parsed.get("message_id", f"<error-{uid}>"),
                                from_email=parsed.get("from_email", "unknown"),
                                from_name=parsed.get("from_name", ""),
                                subject=parsed.get("subject", ""),
                                received_at=parsed.get("received_at"),
                                status=EmailLogStatus.error,
                                error_message=str(e),
                            )
                            err_db.add(err_log)
                            await err_db.commit()

                # Mark as seen (even on error/dup to avoid re-processing)
                if inbound.mark_seen:
                    await client.store(uid, "+FLAGS", r"(\Seen)")

                # Optionally move to processed folder
                if inbound.move_to_folder:
                    await client.copy(uid, inbound.move_to_folder)
                    await client.store(uid, "+FLAGS", r"(\Deleted)")

            except Exception as e:
                logger.error(f"Failed to process email UID {uid}: {e}")
                continue

        if inbound.move_to_folder:
            await client.expunge()

    finally:
        try:
            await client.logout()
        except Exception:
            pass

    return processed


# ── Graph poller ──────────────────────────────────────────────────────────────

async def _poll_graph(inbound: InboundEmailConfig, email_cfg: EmailConfig) -> int:
    processed = 0
    async with AsyncSessionLocal() as db:
        access_token = await _ensure_valid_token(email_cfg, db)
        await db.commit()

    mailbox = inbound.graph_mailbox or ""
    if not mailbox:
        raise RuntimeError("graph_mailbox not configured")

    messages = await _graph_get_unread(mailbox, access_token)
    logger.info(f"Graph poller: found {len(messages)} unread message(s)")

    for msg in messages:
        try:
            from_info = msg.get("from", {}).get("emailAddress", {})
            from_email = from_info.get("address", "unknown@unknown")
            from_name  = from_info.get("name", "")
            subject    = msg.get("subject", "(no subject)")
            body_raw   = msg.get("body", {})
            body_text  = body_raw.get("content", "")
            if body_raw.get("contentType", "").lower() == "html":
                from app.services.email_parser import _strip_html
                body_text = _strip_html(body_text)

            received_str = msg.get("receivedDateTime")
            received_at  = None
            if received_str:
                try:
                    received_at = datetime.fromisoformat(
                        received_str.replace("Z", "+00:00")
                    )
                except Exception:
                    pass

            message_id = msg.get("internetMessageId") or f"<graph-{msg['id']}>"

            async with AsyncSessionLocal() as db:
                try:
                    ticket, log = await _create_ticket_from_email(
                        db, inbound,
                        message_id=message_id,
                        from_email=from_email,
                        from_name=from_name,
                        subject=subject,
                        body=body_text,
                        received_at=received_at,
                    )
                    await db.commit()
                    processed += 1

                    redis = await get_redis()
                    await invalidate_tickets(redis)
                    await broadcast_ticket_event(
                        "ticket_created",
                        {
                            "ticket_id":     str(ticket.id),
                            "ticket_number": ticket.ticket_id,
                            "source":        "email_graph",
                            "from_email":    from_email,
                            # company derived from domain
                            "company": from_email.split("@")[1] if "@" in from_email else "",
                        },
                    )

                except ValueError:
                    pass  # duplicate
                except Exception as e:
                    logger.error(f"Graph ticket creation error: {e}")

            # Mark as read in Graph
            await _graph_mark_read(mailbox, access_token, msg["id"])

        except Exception as e:
            logger.error(f"Graph message processing error: {e}")

    return processed


# ── Main poller class ─────────────────────────────────────────────────────────

class EmailPoller:
    """
    Singleton background service. Call start() on app startup,
    stop() on shutdown.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop(), name="email-poller")
            logger.info("EmailPoller started")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("EmailPoller stopped")

    # ── Background loop ────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            interval = 5  # default minutes
            try:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(InboundEmailConfig))
                    inbound = result.scalar_one_or_none()
                if inbound:
                    interval = inbound.poll_interval_minutes
                    if inbound.enabled:
                        await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EmailPoller loop error: {e}")

            try:
                await asyncio.sleep(interval * 60)
            except asyncio.CancelledError:
                break

    # ── Single poll cycle (also called from API for manual trigger) ────

    async def poll_once(self) -> dict:
        """
        Execute one poll cycle. Returns a result summary dict.
        Raises on misconfiguration.
        """
        async with AsyncSessionLocal() as db:
            inbound_res = await db.execute(select(InboundEmailConfig))
            inbound: InboundEmailConfig | None = inbound_res.scalar_one_or_none()
            email_cfg_res = await db.execute(select(EmailConfig))
            email_cfg: EmailConfig | None = email_cfg_res.scalar_one_or_none()

        if not inbound:
            raise RuntimeError("Inbound email not configured")
        if not inbound.enabled:
            raise RuntimeError("Inbound email is disabled")

        start = datetime.now(timezone.utc)
        processed = 0
        error: str | None = None

        try:
            if inbound.auth_type == InboundAuthType.graph:
                if not email_cfg:
                    raise RuntimeError("OAuth email config missing for Graph API")
                processed = await _poll_graph(inbound, email_cfg)
            else:
                if not inbound.imap_host:
                    raise RuntimeError("IMAP host not configured")
                processed = await _poll_imap(inbound, email_cfg)  # type: ignore[arg-type]

        except Exception as e:
            error = str(e)
            logger.error(f"poll_once error: {e}")

        # Update stats
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(InboundEmailConfig))
            cfg = res.scalar_one_or_none()
            if cfg:
                cfg.last_polled_at = datetime.now(timezone.utc)
                if processed:
                    cfg.processed_count = (cfg.processed_count or 0) + processed
                await db.commit()

        return {
            "polled_at": start.isoformat(),
            "processed": processed,
            "error": error,
            "duration_ms": int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
email_poller = EmailPoller()
