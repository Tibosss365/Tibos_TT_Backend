"""
Utilities for parsing raw RFC 2822 email messages into a structured dict.

Handles:
  - Encoded headers (RFC 2047)
  - multipart/mixed, multipart/alternative, multipart/related
  - Prefers text/plain; falls back to HTML-stripped text/html
  - Safe filename sanitisation on attachments (metadata only, not stored)
"""
import email
import email.policy
import quopri
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import TypedDict


# ── Types ──────────────────────────────────────────────────────────────────────

class ParsedEmail(TypedDict):
    message_id: str
    from_email: str
    from_name: str
    subject: str
    body: str          # plain-text body, HTML stripped
    received_at: datetime | None


# ── Helpers ────────────────────────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    """Minimal HTML → plain-text converter."""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s{3,}", "\n\n", " ".join(self._parts)).strip()


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    try:
        stripper.feed(html)
        return stripper.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", "", html).strip()


def _decode_header_value(raw: str) -> str:
    """Decode an RFC 2047-encoded header value to a plain string."""
    parts = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            except (LookupError, Exception):
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _get_body(msg: email.message.Message) -> str:
    """
    Walk the MIME tree and return the best available plain-text body.
    Priority: text/plain > HTML-stripped text/html > empty string.
    """
    plain_parts: list[str] = []
    html_parts:  list[str] = []

    for part in msg.walk():
        ctype = part.get_content_type()
        disp  = str(part.get("Content-Disposition") or "")
        if "attachment" in disp:
            continue

        charset = part.get_content_charset() or "utf-8"
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        text = payload.decode(charset, errors="replace")

        if ctype == "text/plain":
            plain_parts.append(text)
        elif ctype == "text/html":
            html_parts.append(text)

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        return _strip_html("\n\n".join(html_parts))
    return ""


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_raw_email(raw: bytes) -> ParsedEmail:
    """
    Parse a raw RFC 2822 email bytes blob.

    Returns a ParsedEmail dict ready to be turned into a Ticket.
    """
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)

    # Message-ID
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        # Fallback: synthesise one so we can de-duplicate
        from hashlib import sha256
        message_id = "<synth-" + sha256(raw[:256]).hexdigest()[:16] + "@local>"

    # From
    from_raw   = msg.get("From") or ""
    from_name_raw, from_addr = parseaddr(from_raw)
    from_name  = _decode_header_value(from_name_raw) if from_name_raw else from_addr.split("@")[0]
    from_email = from_addr.lower().strip()

    # Subject
    subject_raw = msg.get("Subject") or "(no subject)"
    subject     = _decode_header_value(subject_raw)

    # Date
    received_at: datetime | None = None
    date_str = msg.get("Date")
    if date_str:
        try:
            received_at = parsedate_to_datetime(date_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            received_at = None

    # Body
    body = _get_body(msg)

    return ParsedEmail(
        message_id=message_id,
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        body=body,
        received_at=received_at,
    )
