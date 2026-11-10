"""Keyword-based "smart filter": tells commercial/sales emails apart from technical ones.

Only the subject and the *new* part of the message are scored. Quoted history
(``From: … Sent: …``, ``On … wrote:``) is cut off, because an old sales thread can
sit under a genuine technical question. When unsure the verdict is technical, so
a real problem is never blocked on a single stray word.
"""
import re
from dataclasses import dataclass, field

from app.services.email_parser import _strip_html

DEFAULT_COMMERCIAL = [
    "quote", "quotation", "purchase", "purchase order", "po number", "proposal",
    "rfp", "request for proposal", "pricing", "price list", "price", "cost estimate",
    "estimate", "invoice", "payment", "renewal", "discount", "offer", "tender",
    "procurement", "negotiation", "commercial", "business opportunity", "partnership",
    "buy", "buying", "order confirmation", "rate card", "demo", "webinar",
    "unsubscribe", "newsletter",
]

DEFAULT_TECHNICAL = [
    "error", "issue", "problem", "incorrect", "not working", "unable", "cannot",
    "can't", "failed", "failure", "login", "log in", "sign in", "password", "reset",
    "locked", "access", "permission", "mfa", "outage", "down", "slow", "sync",
    "mailbox", "outlook", "teams", "sharepoint", "onedrive", "intune", "defender",
    "disabled", "not showing", "showing", "configure", "configuration", "setup",
    "install", "connect", "connection", "vpn", "firewall", "dns", "backup", "restore",
    "bug", "crash", "migration", "migrate",
]

DEFAULT_THRESHOLD = 2
SUBJECT_WEIGHT = 2
MAX_BODY_CHARS = 1500

_QUOTE_MARKERS = re.compile(
    r"(?im)^\s*(?:"
    r"from:\s|sent:\s|-{2,}\s*original message|-{2,}\s*forwarded message|_{5,}|>\s"
    r"|on .{5,120}wrote:"
    r")"
)


@dataclass
class Verdict:
    commercial: bool
    commercial_score: int = 0
    technical_score: int = 0
    matched_commercial: list[str] = field(default_factory=list)
    matched_technical: list[str] = field(default_factory=list)

    def reason(self) -> str:
        words = ", ".join(self.matched_commercial[:6]) or "no keywords"
        return f"looks commercial (matched: {words}; score {self.commercial_score} vs technical {self.technical_score})"


def latest_message_text(body: str) -> str:
    """The text of the newest message only, without the quoted trail below it."""
    text = _strip_html(body) if ("<" in body and ">" in body) else (body or "")
    marker = _QUOTE_MARKERS.search(text)
    fresh = text[: marker.start()] if marker else text
    if not fresh.strip():  # e.g. a bare forward — fall back to what follows
        fresh = text
    return fresh.strip()[:MAX_BODY_CHARS]


def _compile(words: list[str]) -> list[tuple[str, re.Pattern]]:
    out = []
    for word in words:
        word = (word or "").strip().lower()
        if word:
            out.append((word, re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")))
    return out


def _hits(patterns, subject: str, body: str) -> tuple[int, list[str]]:
    score, matched = 0, []
    for word, pattern in patterns:
        in_subject = bool(pattern.search(subject))
        in_body = bool(pattern.search(body))
        if in_subject or in_body:
            matched.append(word)
            score += SUBJECT_WEIGHT if in_subject else 1
    return score, matched


def classify_email(subject: str, body: str, config: dict | None) -> Verdict:
    config = config or {}
    commercial_words = config.get("commercial") or DEFAULT_COMMERCIAL
    technical_words = config.get("technical") or DEFAULT_TECHNICAL
    threshold = int(config.get("threshold") or DEFAULT_THRESHOLD)

    subject_l = (subject or "").lower()
    body_l = latest_message_text(body or "").lower()

    commercial_score, matched_commercial = _hits(_compile(commercial_words), subject_l, body_l)
    technical_score, matched_technical = _hits(_compile(technical_words), subject_l, body_l)

    return Verdict(
        commercial=commercial_score >= threshold and commercial_score > technical_score,
        commercial_score=commercial_score,
        technical_score=technical_score,
        matched_commercial=matched_commercial,
        matched_technical=matched_technical,
    )
