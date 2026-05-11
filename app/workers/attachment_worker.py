"""
Async attachment worker — Redis-backed queue using ARQ.

Why a queue?
────────────
Fetching and uploading attachments is I/O-heavy and can take several seconds
per email.  Doing it inline in the poll loop blocks the next email from being
processed and risks timeouts.  Offloading to a worker means:

  • The poll loop creates the Ticket immediately and returns.
  • Attachments are uploaded and linked asynchronously.
  • Failures are retried without re-processing the email.

Architecture
────────────
  EmailPoller._poll_graph()
      └─ enqueue_attachment_job(ticket_id, graph_msg_id, mailbox, token)
              │  (pushes JSON to Redis queue)
              ▼
  AttachmentWorker.process_attachment_job(ctx, ...)
      └─ fetch_message_attachments()  →  process_all_attachments()

Usage
─────
  # Start the worker process (runs alongside uvicorn, or as a separate container):
  arq app.workers.attachment_worker.WorkerSettings

  # Enqueue from the poller:
  await enqueue_attachment_job(ticket_id, msg_id, mailbox, token)

Dependencies
────────────
  pip install arq

Environment
───────────
  REDIS_URL  — shared with the main app (default: redis://localhost:6379/0)
"""

import logging
import uuid

from app.database import AsyncSessionLocal
from app.services.graph_attachment_client import fetch_message_attachments
from app.services.attachment_service import process_all_attachments

logger = logging.getLogger(__name__)

# ── Job functions ──────────────────────────────────────────────────────────────

async def process_attachment_job(
    ctx: dict,
    ticket_id: str,
    graph_msg_id: str,
    mailbox: str,
    token: str,
) -> dict:
    """
    ARQ job: fetch attachments from Graph and store them.

    Idempotent — safe to retry because process_all_attachments checks for
    existing (ticket_id, filename, size) combos before uploading.

    Args:
        ctx:          ARQ context (contains redis connection).
        ticket_id:    UUID string of the already-created Ticket.
        graph_msg_id: Graph message ID (e.g. "AAMkAGU...").
        mailbox:      Exchange Online mailbox address.
        token:        Valid Graph API bearer token at enqueue time.
                      NOTE: tokens expire in ~1h.  For longer queues, re-acquire
                      the token inside the job using stored credentials instead
                      of passing it here.
    """
    tid = uuid.UUID(ticket_id)
    logger.info("attachment_job started: ticket=%s msg=%s", ticket_id, graph_msg_id)

    try:
        raw_list = await fetch_message_attachments(
            mailbox, token, graph_msg_id, skip_inline=False
        )
    except Exception as exc:
        logger.error("attachment_job: Graph fetch failed for msg %s: %s", graph_msg_id, exc)
        raise  # ARQ will retry

    if not raw_list:
        return {"ticket_id": ticket_id, "processed": 0}

    async with AsyncSessionLocal() as db:
        results = await process_all_attachments(db, tid, raw_list, skip_inline=True)
        await db.commit()

    logger.info(
        "attachment_job done: ticket=%s processed=%d", ticket_id, len(results)
    )
    return {"ticket_id": ticket_id, "processed": len(results)}


# ── Enqueue helper (called from the poller) ────────────────────────────────────

async def enqueue_attachment_job(
    ticket_id: uuid.UUID,
    graph_msg_id: str,
    mailbox: str,
    token: str,
) -> None:
    """
    Push an attachment processing job onto the ARQ queue.

    Import this in email_poller.py and call it after creating the Ticket
    instead of calling _graph_get_attachments inline.
    """
    try:
        import arq  # type: ignore[import]
        from app.config import get_settings

        settings = get_settings()
        redis_settings = arq.connections.RedisSettings.from_dsn(settings.REDIS_URL)
        async with arq.create_pool(redis_settings) as pool:
            await pool.enqueue_job(
                "process_attachment_job",
                str(ticket_id),
                graph_msg_id,
                mailbox,
                token,
                _queue_name="attachments",
            )
        logger.debug(
            "Enqueued attachment job: ticket=%s msg=%s", ticket_id, graph_msg_id
        )
    except Exception as exc:
        logger.error("Failed to enqueue attachment job: %s", exc)
        # Non-fatal — the ticket was created; attachments just won't appear


# ── ARQ worker settings ────────────────────────────────────────────────────────

class WorkerSettings:
    """
    Pass this class to the `arq` CLI:
        arq app.workers.attachment_worker.WorkerSettings
    """

    functions = [process_attachment_job]
    queue_name = "attachments"
    max_jobs = 10
    job_timeout = 300          # 5 minutes per job
    keep_result = 3600         # keep result in Redis for 1 hour
    max_tries = 3              # retry up to 3 times on exception

    @classmethod
    def redis_settings(cls):
        import arq  # type: ignore[import]
        from app.config import get_settings
        return arq.connections.RedisSettings.from_dsn(get_settings().REDIS_URL)
