"""
Outbound email sender for ticket communications.

Sends HTML emails via SMTP (TLS/SSL) using the EmailConfig stored in the DB.
All ticket email events (created, comment, status, resolved) call send_ticket_email().
"""
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import EmailConfig, SMTPSecurity
from app.models.ticket import Ticket

logger = logging.getLogger(__name__)


def _build_html(subject: str, ticket: Ticket, body: str, action_label: str, action_color: str = "#6366f1") -> str:
    """Build a clean HTML email for ticket communication."""
    priority_colors = {
        "critical": "#ef4444",
        "high":     "#f97316",
        "medium":   "#f59e0b",
        "low":      "#6b7280",
    }
    status_colors = {
        "open":        "#3b82f6",
        "in-progress": "#8b5cf6",
        "on-hold":     "#f59e0b",
        "resolved":    "#10b981",
        "closed":      "#6b7280",
    }
    pri_color = priority_colors.get(str(ticket.priority.value if hasattr(ticket.priority, 'value') else ticket.priority), "#6b7280")
    sta_color = status_colors.get(str(ticket.status.value if hasattr(ticket.status, 'value') else ticket.status), "#6b7280")
    pri_val   = ticket.priority.value if hasattr(ticket.priority, 'value') else str(ticket.priority)
    sta_val   = ticket.status.value   if hasattr(ticket.status,   'value') else str(ticket.status)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f4f4f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
        <!-- Header -->
        <tr><td style="background:{action_color};padding:20px 28px;">
          <span style="color:#fff;font-size:13px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;">{action_label}</span>
          <h2 style="color:#fff;margin:6px 0 0;font-size:18px;font-weight:700;">{subject}</h2>
        </td></tr>
        <!-- Ticket meta chips -->
        <tr><td style="padding:16px 28px 0;border-bottom:1px solid #f0f0f4;">
          <span style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;background:{pri_color}22;color:{pri_color};border:1px solid {pri_color}44;margin-right:6px;">{pri_val.upper()}</span>
          <span style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;background:{sta_color}22;color:{sta_color};border:1px solid {sta_color}44;margin-right:6px;">{sta_val.upper()}</span>
          <span style="display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;background:#6366f122;color:#6366f1;border:1px solid #6366f144;">{ticket.ticket_id}</span>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:20px 28px;font-size:14px;color:#374151;line-height:1.7;">
          {body}
        </td></tr>
        <!-- Footer -->
        <tr><td style="padding:16px 28px;background:#f9f9fc;border-top:1px solid #f0f0f4;font-size:11px;color:#9ca3af;">
          This is an automated message from the Help Desk system. Ticket ID: <strong>{ticket.ticket_id}</strong>.<br/>
          Reply to this email to add a comment to the ticket.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_ticket_email(
    db: AsyncSession,
    ticket: Ticket,
    to_email: str,
    subject: str,
    body_html: str,
    action_label: str,
    action_color: str = "#6366f1",
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str | None:
    """
    Send an HTML email for a ticket event.
    Returns the Message-ID of the sent email (for thread tracking), or None on failure.
    Reads EmailConfig from DB; silently logs and returns None if email not configured.
    """
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()

    if not cfg or not cfg.smtp_host:
        logger.debug("Email not configured — skipping ticket email")
        return None

    from_addr = cfg.smtp_from or cfg.smtp_user or ""
    if not from_addr:
        logger.debug("No from address configured — skipping ticket email")
        return None

    html_body = _build_html(subject, ticket, body_html, action_label, action_color)
    msg_id    = make_msgid(domain=(from_addr.split("@")[-1] if "@" in from_addr else "helpdesk"))

    msg = MIMEMultipart("alternative")
    msg["Subject"]    = subject
    msg["From"]       = from_addr
    msg["To"]         = to_email
    msg["Date"]       = formatdate(localtime=False)
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"]  = references or in_reply_to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        port = int(cfg.smtp_port or 587)
        host = cfg.smtp_host or ""

        if cfg.smtp_security == SMTPSecurity.ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
                server.login(cfg.smtp_user or "", cfg.smtp_pass or "")
                server.sendmail(from_addr, [to_email], msg.as_bytes())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                if cfg.smtp_security == SMTPSecurity.tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(cfg.smtp_user or "", cfg.smtp_pass or "")
                server.sendmail(from_addr, [to_email], msg.as_bytes())

        logger.info(f"Email sent to {to_email} for {ticket.ticket_id} [{action_label}]")
        return msg_id

    except Exception as e:
        logger.error(f"Failed to send ticket email to {to_email}: {e}")
        return None
