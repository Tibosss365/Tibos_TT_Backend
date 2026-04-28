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

class ParsedAttachment(TypedDict):
    filename: str
    content_type: str
    content: bytes      # raw file bytes


class ParsedEmail(TypedDict):
    message_id: str
    from_email: str
    from_name: str
    subject: str
    body: str          # plain-text body, HTML stripped
    received_at: datetime | None
    in_reply_to: str   # value of In-Reply-To header (for thread matching)
    references: str    # value of References header (for thread matching)
    attachments: list[ParsedAttachment]


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


_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB per attachment


def _safe_filename(raw: str) -> str:
    """Decode RFC 2047-encoded filename and sanitise path components."""
    name = _decode_header_value(raw) if raw else "attachment"
    return name.replace("/", "_").replace("\\", "_").strip() or "attachment"


def _get_attachments(msg: email.message.Message) -> list[dict]:
    """Return a list of non-inline file attachments found in the MIME tree."""
    attachments: list[dict] = []
    for part in msg.walk():
        disp = str(part.get("Content-Disposition") or "")
        ctype = part.get_content_type()
        # Collect only explicit attachments (not inline body parts or text/html)
        if "attachment" not in disp:
            continue
        raw_name = part.get_filename() or ""
        filename = _safe_filename(raw_name)
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        if len(payload) > _MAX_ATTACHMENT_BYTES:
            continue  # skip files over 10 MB
        attachments.append({
            "filename": filename,
            "content_type": ctype or "application/octet-stream",
            "content": payload,
        })
    return attachments


# ── Disclaimer stripping ───────────────────────────────────────────────────────
# Outlook/Exchange injects a "Caution: This is an external email…" block into
# the HTML body. We strip it at two levels:
#   1. From the raw HTML (before converting to plain text) — catches the block
#      when it is wrapped in its own <div>/<table> element.
#   2. From the resulting plain text — catches any residual text when the HTML
#      block runs directly into the user's message without a tag boundary.

# HTML-level: match a block element whose text content starts with "Caution:"
_HTML_DISCLAIMER_RE = re.compile(
    r"<(?:div|table|td|p|blockquote)[^>]*>"
    r"(?:<[^>]+>)*\s*(?:<b>|<strong>)?\s*Caution\s*:.*?</(?:div|table|td|p|blockquote)>",
    re.IGNORECASE | re.DOTALL,
)

# Plain-text level: from "Caution:" to the end of the known disclaimer sentence.
# The known ending phrase is "contact your IT Department" (with optional trailing text).
_TEXT_DISCLAIMER_PATTERNS = [
    # Matches full Outlook disclaimer including the sentence that ends at "IT Department"
    re.compile(
        r"Caution\s*:?\s*This is an external email.*?(?:contact your IT Department[^.]*\.?|"
        r"in doubt[^\n]*)\s*",
        re.IGNORECASE | re.DOTALL,
    ),
    # Generic gateway banners
    re.compile(
        r"This (message|email|sender) (is from outside|came from outside)[^\n]*\n?",
        re.IGNORECASE,
    ),
]


def _strip_html_disclaimers(html: str) -> str:
    """Remove Outlook caution blocks from raw HTML before plain-text conversion."""
    return _HTML_DISCLAIMER_RE.sub("", html)


def _strip_disclaimers(text: str) -> str:
    """Remove mail-gateway disclaimer text from an already-stripped plain-text body."""
    for pattern in _TEXT_DISCLAIMER_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


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
        cleaned_html = _strip_html_disclaimers("\n\n".join(html_parts))
        return _strip_html(cleaned_html)
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

    # Threading headers (for inbound reply matching)
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    references  = (msg.get("References")  or "").strip()

    # Body
    body = _strip_disclaimers(_get_body(msg))

    # Attachments
    attachments = _get_attachments(msg)

    return ParsedEmail(
        message_id=message_id,
        from_email=from_email,
        from_name=from_name,
        subject=subject,
        body=body,
        received_at=received_at,
        in_reply_to=in_reply_to,
        references=references,
        attachments=attachments,
    )
