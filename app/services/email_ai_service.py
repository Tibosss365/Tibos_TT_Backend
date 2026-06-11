"""
AI helpers for the email inbox: reply suggestions and thread summaries.

Powered by the Anthropic API. Entirely optional — when ANTHROPIC_API_KEY is
not configured (or the `anthropic` package is missing) callers get an
AIUnavailableError, which the router maps to a 503 so the UI can degrade
gracefully.
"""
import json
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class AIUnavailableError(RuntimeError):
    """AI features are not configured on this deployment."""


def _get_client():
    if not settings.ANTHROPIC_API_KEY:
        raise AIUnavailableError(
            "AI features are not configured — set ANTHROPIC_API_KEY on the server."
        )
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise AIUnavailableError(
            "AI features are not available — the 'anthropic' package is not installed."
        )
    return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


async def suggest_reply(
    *,
    message_subject: str,
    message_body: str,
    from_name: str,
    thread_context: list[dict],
    tone: str = "professional",
    language: str = "en",
    max_length: int | None = None,
) -> str:
    """Draft a reply to an inbound support email. Returns plain text."""
    client = _get_client()

    context_lines = []
    for m in thread_context[-6:]:
        who = "Agent" if m.get("direction") == "outbound" else (m.get("from_name") or m.get("from_email") or "Customer")
        context_lines.append(f"[{who}]: {(m.get('body') or '')[:1500]}")

    length_hint = f" Keep it under roughly {max_length} words." if max_length else ""
    prompt = (
        f"You are drafting a reply for an IT helpdesk agent.\n\n"
        f"Earlier messages in this thread (oldest first):\n"
        + ("\n---\n".join(context_lines) if context_lines else "(none)")
        + f"\n\nLatest message from {from_name or 'the customer'} "
        f"(subject: {message_subject or '(no subject)'}):\n{message_body[:6000]}\n\n"
        f"Write a {tone} reply in language '{language}'. Output ONLY the reply body "
        f"as plain text — no subject line, no signature, no commentary.{length_hint}"
    )

    response = await client.messages.create(
        model=settings.AI_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


async def summarize_thread(
    *,
    subject: str,
    messages: list[dict],
    max_length: int | None = None,
) -> dict:
    """Summarize an email thread. Returns {summary, sentiment, key_points}."""
    client = _get_client()

    lines = []
    for m in messages[-12:]:
        who = "Agent" if m.get("direction") == "outbound" else (m.get("from_name") or m.get("from_email") or "Customer")
        lines.append(f"[{who}]: {(m.get('body') or '')[:2000]}")

    length_hint = f" The summary must be at most {max_length} words." if max_length else ""
    prompt = (
        f"Summarize this IT helpdesk email thread (subject: {subject or '(no subject)'}).\n\n"
        + "\n---\n".join(lines)
        + "\n\nRespond with ONLY a JSON object, no markdown fences, in this exact shape:\n"
        '{"summary": "<2-4 sentence summary>", '
        '"sentiment": "<positive|neutral|negative — the customer\'s sentiment>", '
        f'"key_points": ["<up to 5 short bullet points>"]}}{length_hint}'
    )

    response = await client.messages.create(
        model=settings.AI_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text").strip()

    try:
        # Tolerate accidental code fences around the JSON
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        data = json.loads(text)
        sentiment = data.get("sentiment")
        if sentiment not in ("positive", "neutral", "negative"):
            sentiment = None
        return {
            "summary": str(data.get("summary") or "")[:4000],
            "sentiment": sentiment,
            "key_points": [str(p)[:300] for p in (data.get("key_points") or [])][:5],
        }
    except (json.JSONDecodeError, AttributeError):
        logger.warning("[email-ai] summarize returned non-JSON; using raw text")
        return {"summary": text[:4000], "sentiment": None, "key_points": []}
