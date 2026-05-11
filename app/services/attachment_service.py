"""
Attachment service — orchestration layer.

Responsibilities:
  1. Accept raw attachment bytes (from Graph or IMAP parser).
  2. Check idempotency (skip if already stored for this ticket/filename/size).
  3. Upload bytes to the configured storage backend.
  4. Persist only metadata to the DB via AttachmentRepository.
  5. On failure, attempt to clean up the storage object.

This service is intentionally decoupled from the Graph API client so it can
also be called for IMAP attachments or future sources.

Async queue integration
───────────────────────
For high-volume deployments, call `enqueue_attachment_job` instead of
`process_attachment` directly.  The worker picks it up from the Redis queue
and calls `process_attachment` there.  See attachment_worker.py.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.attachment_repository import AttachmentCreate, AttachmentRepository
from app.services.attachment_storage import StorageBackend, build_storage_key, get_storage_backend
from app.services.graph_attachment_client import RawAttachment

logger = logging.getLogger(__name__)


@dataclass
class ProcessedAttachment:
    id: uuid.UUID
    ticket_id: uuid.UUID
    filename: str
    content_type: str
    size: int
    storage_key: str
    storage_url: str
    is_inline: bool


async def process_attachment(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    raw: RawAttachment,
    storage: StorageBackend | None = None,
) -> ProcessedAttachment | None:
    """
    Upload *raw* to object storage and save metadata to DB.

    Returns None when the attachment is skipped (duplicate or upload failure).
    The caller must commit the session.
    """
    if storage is None:
        storage = get_storage_backend()

    repo = AttachmentRepository(db)

    # ── Idempotency guard ─────────────────────────────────────────────────────
    if await repo.exists(ticket_id, raw.filename, raw.size):
        logger.debug(
            "Skipping duplicate attachment '%s' (%d bytes) for ticket %s",
            raw.filename, raw.size, ticket_id,
        )
        return None

    # ── Upload to object storage ───────────────────────────────────────────────
    storage_key = build_storage_key(str(ticket_id), raw.filename)
    try:
        await storage.upload(storage_key, raw.content, raw.content_type)
    except Exception as exc:
        logger.error(
            "Storage upload failed for '%s' (ticket %s): %s",
            raw.filename, ticket_id, exc,
        )
        return None

    # ── Resolve the permanent/presigned URL ───────────────────────────────────
    # LocalFileBackend raises NotImplementedError for both public_url and
    # presigned_url — files are served directly via the API download endpoint,
    # so we store an empty string and let the download endpoint do the work.
    try:
        storage_url = await storage.public_url(storage_key)
    except NotImplementedError:
        try:
            storage_url = await storage.presigned_url(storage_key, expires_seconds=86400 * 30)
        except NotImplementedError:
            storage_url = ""  # local backend: served via API /tickets/.../attachments/...

    # ── Persist metadata ──────────────────────────────────────────────────────
    try:
        att = await repo.create(
            AttachmentCreate(
                ticket_id=ticket_id,
                filename=raw.filename,
                content_type=raw.content_type,
                size=raw.size,
                storage_key=storage_key,
                storage_url=storage_url,
                is_inline=raw.is_inline,
            )
        )
    except Exception as exc:
        logger.error(
            "DB persist failed for '%s' (ticket %s): %s — cleaning up storage",
            raw.filename, ticket_id, exc,
        )
        # Best-effort: remove the orphaned object from storage
        try:
            await storage.delete(storage_key)
        except Exception:
            pass
        return None

    logger.info(
        "Attachment '%s' stored at %s (ticket %s, %d bytes)",
        raw.filename, storage_key, ticket_id, raw.size,
    )
    return ProcessedAttachment(
        id=att.id,
        ticket_id=ticket_id,
        filename=raw.filename,
        content_type=raw.content_type,
        size=raw.size,
        storage_key=storage_key,
        storage_url=storage_url,
        is_inline=raw.is_inline,
    )


async def process_all_attachments(
    db: AsyncSession,
    ticket_id: uuid.UUID,
    raw_attachments: list[RawAttachment],
    *,
    skip_inline: bool = True,
    storage: StorageBackend | None = None,
) -> list[ProcessedAttachment]:
    """
    Process a list of raw attachments for a ticket.
    Returns successfully processed results (failed/duplicate ones are omitted).
    """
    results: list[ProcessedAttachment] = []
    for raw in raw_attachments:
        if skip_inline and raw.is_inline:
            continue
        result = await process_attachment(db, ticket_id, raw, storage=storage)
        if result is not None:
            results.append(result)
    return results


async def delete_attachment(
    db: AsyncSession,
    attachment_id: uuid.UUID,
    storage: StorageBackend | None = None,
) -> bool:
    """
    Delete an attachment from both storage and the DB.
    Returns True if the attachment was found and deleted.
    """
    if storage is None:
        storage = get_storage_backend()

    repo = AttachmentRepository(db)
    att = await repo.get_by_id(attachment_id)
    if att is None:
        return False

    if att.storage_key:
        try:
            await storage.delete(att.storage_key)
        except Exception as exc:
            logger.warning("Storage delete failed for key %s: %s", att.storage_key, exc)

    await repo.delete_by_id(attachment_id)
    return True


async def get_download_url(
    db: AsyncSession,
    attachment_id: uuid.UUID,
    expires_seconds: int = 3600,
    storage: StorageBackend | None = None,
) -> str | None:
    """
    Generate a fresh presigned download URL for an attachment.
    Returns None if the attachment doesn't exist.
    """
    if storage is None:
        storage = get_storage_backend()

    repo = AttachmentRepository(db)
    att = await repo.get_by_id(attachment_id)
    if att is None or not att.storage_key:
        return None

    try:
        return await storage.presigned_url(att.storage_key, expires_seconds)
    except NotImplementedError:
        return att.storage_url
