"""
CSAT (Customer Satisfaction) survey service.

When a ticket is resolved:
  1. Generate a unique token.
  2. Store it on the ticket (csat_token, csat_sent_at).
  3. Send a survey email to the requester (if email is available).

Survey submission is handled by the public CSAT router (no auth required).
"""
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")


async def send_csat_survey(ticket, db: AsyncSession, base_url: str) -> None:
    """
    Issue a CSAT survey for *ticket* if:
      - ticket has a customer email
      - csat_token is not already set (i.e. survey not already sent)
    """
    if not ticket.email:
        return
    if ticket.csat_token:
        return  # already sent

    token = secrets.token_urlsafe(32)
    ticket.csat_token = token
    ticket.csat_sent_at = datetime.now(timezone.utc)

    survey_url = f"{base_url}/csat/{token}"

    subject = f"How did we do? – Ticket {ticket.ticket_id}"
    body_html = (
        f"<p>Dear {ticket.contact_name or ticket.submitter_name or 'Customer'},</p>"
        f"<p>Your support ticket <strong>'{ticket.subject}'</strong> has been resolved.</p>"
        f"<p>We'd love to hear how we did!<br>"
        f"<a href='{survey_url}' style='display:inline-block;padding:10px 20px;background:#6366f1;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;'>Rate Your Experience</a></p>"
        f"<p style='font-size:12px;color:#666;'>Or copy this link: {survey_url}</p>"
        f"<p>Thank you,<br>Support Team</p>"
    )

    try:
        # Use the existing send_ticket_email infrastructure if available
        from app.services.email_sender import send_ticket_email
        await send_ticket_email(
            db=db,
            ticket=ticket,
            to_email=ticket.email,
            subject=subject,
            body_html=body_html,
        )
        logger.info(f"[CSAT] Survey email sent for ticket {ticket.ticket_id} → {ticket.email}")
    except Exception as exc:
        # Email not configured or failed — log the URL so admins can share manually
        logger.warning(f"[CSAT] Failed to send survey email for {ticket.ticket_id}: {exc}")
        logger.info(f"[CSAT] Survey URL for {ticket.ticket_id}: {survey_url}")
        # Don't abort — token is still saved so manual re-send is possible
