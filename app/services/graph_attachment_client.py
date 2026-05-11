"""
Microsoft Graph API attachment client.

Handles two attachment paths:
  • Small  (<= LARGE_ATTACHMENT_THRESHOLD_BYTES): contentBytes is already
    present in the /attachments list response — decode Base64 in-memory.
  • Large  (>  LARGE_ATTACHMENT_THRESHOLD_BYTES): contentBytes is absent or
    the @odata.mediaContentType hint is present — stream via /$value.

Inline attachments (isInline=True) are returned tagged so callers can
decide whether to skip them.

All public functions return plain dicts so callers have no Graph dependency.

Retry strategy: exponential back-off with jitter on 429/5xx responses.
"""

import asyncio
import base64
import logging
import random
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

# Attachments whose `size` field exceeds this are fetched via /$value stream
# to avoid loading multi-megabyte Base64 payloads into a single JSON response.
LARGE_ATTACHMENT_THRESHOLD_BYTES: int = 3 * 1024 * 1024  # 3 MB

# Hard ceiling — attachments above this are silently skipped
MAX_ATTACHMENT_BYTES: int = 25 * 1024 * 1024  # 25 MB

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Retry config
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class RawAttachment:
    filename: str
    content_type: str
    size: int
    content: bytes
    is_inline: bool = False
    graph_attachment_id: str = ""


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _make_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    timeout: float = 30.0,
) -> httpx.Response:
    """GET with exponential back-off on transient errors."""
    delay = 1.0
    for attempt in range(1, _MAX_RETRIES + 1):
        resp = await client.get(url, headers=headers, timeout=timeout)
        if resp.status_code not in _RETRY_STATUSES:
            return resp
        if attempt == _MAX_RETRIES:
            return resp
        jitter = random.uniform(0, delay * 0.3)
        wait = delay + jitter
        logger.warning(
            "Graph API %s on %s — retry %d/%d in %.1fs",
            resp.status_code, url, attempt, _MAX_RETRIES, wait,
        )
        await asyncio.sleep(wait)
        delay = min(delay * 2, 30.0)
    return resp  # unreachable but keeps mypy happy


async def _stream_large_attachment(
    mailbox: str,
    token: str,
    msg_id: str,
    att_id: str,
    expected_size: int,
) -> bytes | None:
    """
    Download a large attachment via the /$value endpoint (raw bytes stream).
    Returns None on any failure so the caller can skip gracefully.
    """
    url = (
        f"{GRAPH_BASE}/users/{mailbox}"
        f"/messages/{msg_id}/attachments/{att_id}/$value"
    )
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", url, headers=_make_headers(token), timeout=120.0
            ) as resp:
                if resp.status_code != 200:
                    logger.warning(
                        "Large attachment stream %s returned %s",
                        att_id, resp.status_code,
                    )
                    return None
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_ATTACHMENT_BYTES:
                        logger.warning(
                            "Attachment %s exceeds %d bytes — skipping",
                            att_id, MAX_ATTACHMENT_BYTES,
                        )
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
    except Exception as exc:
        logger.warning("Failed to stream attachment %s: %s", att_id, exc)
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

async def fetch_message_attachments(
    mailbox: str,
    token: str,
    msg_id: str,
    *,
    skip_inline: bool = False,
) -> list[RawAttachment]:
    """
    Fetch all file attachments for a single Graph message.

    Args:
        mailbox:     The Exchange Online mailbox address.
        token:       A valid Graph API access token.
        msg_id:      The Graph message id.
        skip_inline: When True, embedded inline images are excluded.

    Returns a list of RawAttachment objects (content already decoded).
    """
    url = (
        f"{GRAPH_BASE}/users/{mailbox}"
        f"/messages/{msg_id}/attachments"
        "?$select=id,name,contentType,size,contentBytes,isInline,@odata.type"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await _get_with_retry(client, url, _make_headers(token))

        if resp.status_code == 404:
            logger.debug("No attachments endpoint for message %s", msg_id)
            return []
        if resp.status_code != 200:
            logger.warning(
                "Graph attachments list %s returned %s", msg_id, resp.status_code
            )
            return []

        items = resp.json().get("value", [])

    except Exception as exc:
        logger.warning("Graph attachment list failed for %s: %s", msg_id, exc)
        return []

    results: list[RawAttachment] = []

    for item in items:
        odata_type = item.get("@odata.type", "")
        if "#microsoft.graph.fileAttachment" not in odata_type:
            # Skip itemAttachment (embedded messages) and referenceAttachment
            continue

        is_inline = bool(item.get("isInline", False))
        if skip_inline and is_inline:
            continue

        att_id       = item.get("id", "")
        filename     = item.get("name") or "attachment"
        content_type = item.get("contentType") or "application/octet-stream"
        size         = int(item.get("size") or 0)

        if size > MAX_ATTACHMENT_BYTES:
            logger.info(
                "Skipping attachment '%s' (%d bytes) — exceeds %d byte limit",
                filename, size, MAX_ATTACHMENT_BYTES,
            )
            continue

        content: bytes | None = None
        raw_b64 = item.get("contentBytes")

        if raw_b64:
            # Small attachment — already in the list response
            try:
                content = base64.b64decode(raw_b64)
            except Exception as exc:
                logger.warning("Base64 decode failed for '%s': %s", filename, exc)
                continue
        else:
            # contentBytes absent → large attachment, fetch via /$value
            if size > LARGE_ATTACHMENT_THRESHOLD_BYTES or not raw_b64:
                logger.debug(
                    "Fetching large attachment '%s' (%d bytes) via /$value",
                    filename, size,
                )
                content = await _stream_large_attachment(
                    mailbox, token, msg_id, att_id, size
                )
                if content is None:
                    continue

        if not content:
            continue

        results.append(
            RawAttachment(
                filename=filename,
                content_type=content_type,
                size=len(content),
                content=content,
                is_inline=is_inline,
                graph_attachment_id=att_id,
            )
        )

    return results


async def message_has_attachments(msg: dict) -> bool:
    """
    Quick check based on the message metadata dict returned by the Graph
    /messages list endpoint.  Does NOT make an extra API call.
    """
    return bool(msg.get("hasAttachments", False))
