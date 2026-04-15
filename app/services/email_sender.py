"""
Outbound email sender for ticket communications.

Sends HTML emails via SMTP (TLS/SSL) or Microsoft Graph (M365/OAuth)
using the EmailConfig stored in the DB.

All ticket email events (created, comment, status, resolved) call send_ticket_email().
The /admin/email/test endpoint calls send_test_email() to verify config.
"""
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import EmailConfig, EmailType, SMTPSecurity
from app.models.ticket import Ticket

logger = logging.getLogger(__name__)

# ── HTML template builder ─────────────────────────────────────────────────────

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


def _build_test_html(from_addr: str, method: str) -> str:
    """Build the HTML body for a test email."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f4f4f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
        <tr><td style="background:#6366f1;padding:24px 32px;">
          <div style="color:#fff;font-size:13px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;">Test Email</div>
          <h2 style="color:#fff;margin:8px 0 0;font-size:20px;font-weight:700;">✅ Email Configuration Working</h2>
        </td></tr>
        <tr><td style="padding:28px 32px;font-size:14px;color:#374151;line-height:1.8;">
          <p>Your <strong>helpdesk email configuration</strong> is working correctly.</p>
          <table cellpadding="0" cellspacing="0" style="width:100%;margin:16px 0;background:#f8f9ff;border-radius:8px;border:1px solid #e8eaf6;">
            <tr>
              <td style="padding:10px 16px;font-size:12px;color:#6b7280;border-bottom:1px solid #eee;font-weight:600;text-transform:uppercase;letter-spacing:.5px;">Method</td>
              <td style="padding:10px 16px;font-size:13px;color:#374151;border-bottom:1px solid #eee;font-weight:600;">{method}</td>
            </tr>
            <tr>
              <td style="padding:10px 16px;font-size:12px;color:#6b7280;border-bottom:1px solid #eee;font-weight:600;text-transform:uppercase;letter-spacing:.5px;">Sent From</td>
              <td style="padding:10px 16px;font-size:13px;color:#374151;border-bottom:1px solid #eee;">{from_addr}</td>
            </tr>
            <tr>
              <td style="padding:10px 16px;font-size:12px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;">Sent At</td>
              <td style="padding:10px 16px;font-size:13px;color:#374151;">{now}</td>
            </tr>
          </table>
          <p style="color:#6b7280;font-size:13px;">You can now save your settings. Notifications and ticket emails will be delivered through this configuration.</p>
        </td></tr>
        <tr><td style="padding:16px 32px;background:#f9f9fc;border-top:1px solid #f0f0f4;font-size:11px;color:#9ca3af;text-align:center;">
          Helpdesk System — Email Configuration Test
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Graph API token helper ──────────────────────────────────────────────────

async def _get_graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Get an OAuth2 client-credentials token from Microsoft identity platform."""
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "scope":         "https://graph.microsoft.com/.default",
            "grant_type":    "client_credentials",
        })
    if resp.status_code != 200:
        raise RuntimeError(f"Token request failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


async def _send_via_graph(token: str, from_email: str, to_email: str, subject: str, html_body: str) -> None:
    """Send an email via Microsoft Graph API /sendMail."""
    url = f"https://graph.microsoft.com/v1.0/users/{from_email}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        },
        "saveToSentItems": True,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        })
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Graph sendMail failed ({resp.status_code}): {resp.text}")


# ── Test email functions ────────────────────────────────────────────────────

async def send_test_via_smtp(cfg: EmailConfig, to_email: str) -> None:
    """Send a test email using stored SMTP credentials. Raises on any failure."""
    host      = cfg.smtp_host or ""
    port      = int(cfg.smtp_port or 587)
    from_addr = cfg.smtp_from or cfg.smtp_user or ""
    user      = cfg.smtp_user or ""
    password  = cfg.smtp_pass or ""

    if not host:
        raise ValueError("SMTP host is not configured. Save your SMTP settings first.")
    if not from_addr:
        raise ValueError("From address is not configured. Set 'From Address' in SMTP settings.")
    if not user or not password:
        raise ValueError("SMTP username or password is missing.")

    html_body = _build_test_html(from_addr, "SMTP")
    msg_id = make_msgid(domain=(from_addr.split("@")[-1] if "@" in from_addr else "helpdesk"))

    msg = MIMEMultipart("alternative")
    msg["Subject"]    = "✅ Test Email — Helpdesk Configuration Verified"
    msg["From"]       = from_addr
    msg["To"]         = to_email
    msg["Date"]       = formatdate(localtime=False)
    msg["Message-ID"] = msg_id
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if cfg.smtp_security == SMTPSecurity.ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
                server.login(user, password)
                server.sendmail(from_addr, [to_email], msg.as_bytes())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                if cfg.smtp_security == SMTPSecurity.tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(user, password)
                server.sendmail(from_addr, [to_email], msg.as_bytes())
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("SMTP authentication failed — check username and password.")
    except smtplib.SMTPConnectError:
        raise RuntimeError(f"Could not connect to SMTP server {host}:{port} — check host and port.")
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP error: {e}")
    except OSError as e:
        raise RuntimeError(f"Network error connecting to {host}:{port} — {e}")


async def send_test_via_m365(cfg: EmailConfig, to_email: str) -> None:
    """Send a test email via Microsoft Graph API (client credentials). Raises on failure."""
    tenant_id     = cfg.m365_tenant_id     or ""
    client_id     = cfg.m365_client_id     or ""
    client_secret = cfg.m365_client_secret or ""
    from_email    = cfg.m365_from          or ""

    if not tenant_id:
        raise ValueError("M365 Tenant ID is not configured.")
    if not client_id:
        raise ValueError("M365 Client (Application) ID is not configured.")
    if not client_secret:
        raise ValueError("M365 Client Secret is not configured.")
    if not from_email:
        raise ValueError("M365 From Address is not configured.")

    try:
        token = await _get_graph_token(tenant_id, client_id, client_secret)
    except RuntimeError as e:
        raise RuntimeError(f"Failed to obtain Microsoft access token: {e}")

    html_body = _build_test_html(from_email, "Microsoft 365 (Graph API)")
    await _send_via_graph(
        token     = token,
        from_email= from_email,
        to_email  = to_email,
        subject   = "✅ Test Email — Helpdesk M365 Configuration Verified",
        html_body = html_body,
    )


async def send_test_via_oauth(cfg: EmailConfig, to_email: str) -> None:
    """
    Send a test email using an already-authorized OAuth access token.
    For Microsoft OAuth, this uses the Graph API.
    For Google OAuth (Gmail), this uses the Gmail SMTP with XOAUTH2.
    Raises on failure.
    """
    from_email   = cfg.oauth_from          or ""
    access_token = cfg.oauth_access_token  or ""
    provider     = str(cfg.oauth_provider.value if cfg.oauth_provider else "")

    if not from_email:
        raise ValueError("OAuth From Address is not configured.")
    if not access_token:
        raise ValueError(
            "OAuth is not authorized yet. Click 'Authorize' in the OAuth settings to connect your account first."
        )

    # Check token expiry
    if cfg.oauth_token_expiry:
        exp = cfg.oauth_token_expiry
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= exp:
            raise ValueError(
                "OAuth access token has expired. Re-authorize in the Email settings to get a new token."
            )

    html_body = _build_test_html(from_email, f"OAuth 2.0 ({provider or 'custom'})")

    if provider in ("microsoft", ""):
        # Use Microsoft Graph API with the oauth access token
        await _send_via_graph(
            token     = access_token,
            from_email= from_email,
            to_email  = to_email,
            subject   = "✅ Test Email — Helpdesk OAuth Configuration Verified",
            html_body = html_body,
        )
    elif provider == "google":
        # Gmail SMTP with XOAUTH2
        msg_id = make_msgid(domain=(from_email.split("@")[-1] if "@" in from_email else "helpdesk"))
        msg = MIMEMultipart("alternative")
        msg["Subject"]    = "✅ Test Email — Helpdesk OAuth Configuration Verified"
        msg["From"]       = from_email
        msg["To"]         = to_email
        msg["Date"]       = formatdate(localtime=False)
        msg["Message-ID"] = msg_id
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        import base64
        auth_string = f"user={from_email}\x01auth=Bearer {access_token}\x01\x01"
        auth_b64 = base64.b64encode(auth_string.encode()).decode()

        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                server.docmd("AUTH", f"XOAUTH2 {auth_b64}")
                server.sendmail(from_email, [to_email], msg.as_bytes())
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError("Gmail XOAUTH2 authentication failed — ensure Mail.Send scope was granted.")
        except Exception as e:
            raise RuntimeError(f"Gmail OAuth send failed: {e}")
    else:
        raise ValueError(f"Unsupported OAuth provider '{provider}' for test email.")


# ── send_test_from_request — uses credentials from request body ────────────

async def send_test_from_request(req: "EmailTestRequest") -> None:  # type: ignore[name-defined]
    """
    Send a test email using credentials provided directly in the API request.
    The DB is NOT read — all secrets come from the frontend form state.
    Dispatches to SMTP, M365, or OAuth based on req.type.
    """
    email_type = (req.type or "smtp").lower()

    if email_type == "smtp":
        # Validate required fields
        if not req.smtp_host:
            raise ValueError("SMTP Host is required. Fill in the host field and try again.")
        if not req.smtp_from:
            raise ValueError("From Address is required. Fill in the 'From Address' field.")
        if not req.smtp_user:
            raise ValueError("SMTP Username is required.")
        if not req.smtp_pass:
            raise ValueError("SMTP Password is required.")

        from_addr = req.smtp_from
        port      = int(req.smtp_port or 587)
        host      = req.smtp_host
        security  = (req.smtp_security or "tls").lower()

        html_body = _build_test_html(from_addr, "SMTP")
        msg_id = make_msgid(domain=(from_addr.split("@")[-1] if "@" in from_addr else "helpdesk"))

        msg = MIMEMultipart("alternative")
        msg["Subject"]    = "✅ Test Email — Helpdesk Configuration Verified"
        msg["From"]       = from_addr
        msg["To"]         = str(req.to_email)
        msg["Date"]       = formatdate(localtime=False)
        msg["Message-ID"] = msg_id
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            if security == "ssl":
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
                    server.login(req.smtp_user, req.smtp_pass)
                    server.sendmail(from_addr, [str(req.to_email)], msg.as_bytes())
            else:
                with smtplib.SMTP(host, port, timeout=15) as server:
                    server.ehlo()
                    if security == "tls":
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    server.login(req.smtp_user, req.smtp_pass)
                    server.sendmail(from_addr, [str(req.to_email)], msg.as_bytes())
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError("SMTP authentication failed — check username and password.")
        except smtplib.SMTPConnectError:
            raise RuntimeError(f"Could not connect to SMTP server {host}:{port} — check host and port.")
        except smtplib.SMTPException as e:
            raise RuntimeError(f"SMTP error: {e}")
        except OSError as e:
            raise RuntimeError(f"Network error connecting to {host}:{port} — {e}")

    elif email_type == "m365":
        if not req.m365_tenant_id:
            raise ValueError("M365 Tenant ID is required.")
        if not req.m365_client_id:
            raise ValueError("M365 Client (Application) ID is required.")
        if not req.m365_client_secret:
            raise ValueError("M365 Client Secret is required.")
        if not req.m365_from:
            raise ValueError("M365 From Address is required.")

        try:
            token = await _get_graph_token(req.m365_tenant_id, req.m365_client_id, req.m365_client_secret)
        except RuntimeError as e:
            raise RuntimeError(f"Failed to obtain Microsoft access token: {e}")

        html_body = _build_test_html(req.m365_from, "Microsoft 365 (Graph API)")
        await _send_via_graph(
            token      = token,
            from_email = req.m365_from,
            to_email   = str(req.to_email),
            subject    = "✅ Test Email — Helpdesk M365 Configuration Verified",
            html_body  = html_body,
        )

    elif email_type == "oauth":
        if not req.oauth_from:
            raise ValueError("OAuth From Address is required.")
        if not req.oauth_access_token:
            raise ValueError(
                "OAuth is not authorized yet. Click 'Authorize' in the OAuth settings to connect your account first."
            )

        provider = (req.oauth_provider or "").lower()
        html_body = _build_test_html(req.oauth_from, f"OAuth 2.0 ({provider or 'custom'})")

        if provider in ("microsoft", ""):
            await _send_via_graph(
                token      = req.oauth_access_token,
                from_email = req.oauth_from,
                to_email   = str(req.to_email),
                subject    = "✅ Test Email — Helpdesk OAuth Configuration Verified",
                html_body  = html_body,
            )
        elif provider == "google":
            import base64
            from_email = req.oauth_from
            auth_string = f"user={from_email}\x01auth=Bearer {req.oauth_access_token}\x01\x01"
            auth_b64 = base64.b64encode(auth_string.encode()).decode()

            msg_id = make_msgid(domain=(from_email.split("@")[-1] if "@" in from_email else "helpdesk"))
            msg = MIMEMultipart("alternative")
            msg["Subject"]    = "✅ Test Email — Helpdesk OAuth Configuration Verified"
            msg["From"]       = from_email
            msg["To"]         = str(req.to_email)
            msg["Date"]       = formatdate(localtime=False)
            msg["Message-ID"] = msg_id
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            try:
                ctx = ssl.create_default_context()
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                    server.ehlo()
                    server.starttls(context=ctx)
                    server.ehlo()
                    server.docmd("AUTH", f"XOAUTH2 {auth_b64}")
                    server.sendmail(from_email, [str(req.to_email)], msg.as_bytes())
            except smtplib.SMTPAuthenticationError:
                raise RuntimeError("Gmail XOAUTH2 authentication failed — ensure Mail.Send scope was granted.")
            except Exception as e:
                raise RuntimeError(f"Gmail OAuth send failed: {e}")
        else:
            raise ValueError(f"Unsupported OAuth provider '{provider}'.")

    else:
        raise ValueError(f"Unknown email type '{email_type}'. Must be 'smtp', 'm365', or 'oauth'.")


async def send_test_email(db: AsyncSession, to_email: str) -> None:
    """
    Read EmailConfig from DB and send a test email using the configured method.
    Raises ValueError (bad config) or RuntimeError (send failure) on any problem.
    """
    result = await db.execute(select(EmailConfig))
    cfg: Optional[EmailConfig] = result.scalar_one_or_none()

    if not cfg:
        raise ValueError("Email is not configured yet. Go to Admin → Email and enter your settings.")

    email_type = cfg.type.value if cfg.type else "smtp"

    if email_type == "smtp":
        await send_test_via_smtp(cfg, to_email)
    elif email_type == "m365":
        await send_test_via_m365(cfg, to_email)
    elif email_type == "oauth":
        await send_test_via_oauth(cfg, to_email)
    else:
        raise ValueError(f"Unknown email type '{email_type}'.")


# ── Ticket email (existing functionality) ──────────────────────────────────

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

    if not cfg or not cfg.type:
        logger.debug("Email not configured — skipping ticket email")
        return None

    email_type = cfg.type.value if cfg.type else "smtp"

    try:
        if email_type == "smtp":
            return await _send_ticket_via_smtp(cfg, ticket, to_email, subject, body_html, action_label, action_color, in_reply_to, references)
        elif email_type == "m365":
            return await _send_ticket_via_m365(cfg, ticket, to_email, subject, body_html, action_label, action_color)
        elif email_type == "oauth":
            return await _send_ticket_via_oauth(cfg, ticket, to_email, subject, body_html, action_label, action_color)
        else:
            logger.warning(f"Unknown email type '{email_type}' — skipping ticket email")
            return None
    except Exception as e:
        logger.error(f"Failed to send ticket email to {to_email}: {e}")
        return None


async def _send_ticket_via_smtp(
    cfg: EmailConfig,
    ticket: Ticket,
    to_email: str,
    subject: str,
    body_html: str,
    action_label: str,
    action_color: str,
    in_reply_to: Optional[str],
    references: Optional[str],
) -> Optional[str]:
    from_addr = cfg.smtp_from or cfg.smtp_user or ""
    if not from_addr or not cfg.smtp_host:
        logger.debug("SMTP not fully configured — skipping ticket email")
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

    logger.info(f"Email sent to {to_email} for {ticket.ticket_id} [{action_label}] via SMTP")
    return msg_id


async def _send_ticket_via_m365(
    cfg: EmailConfig,
    ticket: Ticket,
    to_email: str,
    subject: str,
    body_html: str,
    action_label: str,
    action_color: str,
) -> Optional[str]:
    if not cfg.m365_tenant_id or not cfg.m365_client_id or not cfg.m365_client_secret or not cfg.m365_from:
        logger.debug("M365 not fully configured — skipping ticket email")
        return None

    token = await _get_graph_token(cfg.m365_tenant_id, cfg.m365_client_id, cfg.m365_client_secret)
    html_body = _build_html(subject, ticket, body_html, action_label, action_color)
    msg_id = make_msgid(domain=(cfg.m365_from.split("@")[-1] if "@" in cfg.m365_from else "helpdesk"))

    await _send_via_graph(
        token=token,
        from_email=cfg.m365_from,
        to_email=to_email,
        subject=subject,
        html_body=html_body,
    )

    logger.info(f"Email sent to {to_email} for {ticket.ticket_id} [{action_label}] via M365 Graph")
    return msg_id


async def _send_ticket_via_oauth(
    cfg: EmailConfig,
    ticket: Ticket,
    to_email: str,
    subject: str,
    body_html: str,
    action_label: str,
    action_color: str,
) -> Optional[str]:
    if not cfg.oauth_access_token or not cfg.oauth_from:
        logger.debug("OAuth not authorized — skipping ticket email")
        return None

    provider = str(cfg.oauth_provider.value if cfg.oauth_provider else "")
    html_body = _build_html(subject, ticket, body_html, action_label, action_color)
    msg_id = make_msgid(domain=(cfg.oauth_from.split("@")[-1] if "@" in cfg.oauth_from else "helpdesk"))

    if provider in ("microsoft", ""):
        await _send_via_graph(
            token=cfg.oauth_access_token,
            from_email=cfg.oauth_from,
            to_email=to_email,
            subject=subject,
            html_body=html_body,
        )
    elif provider == "google":
        import base64
        from_email = cfg.oauth_from
        auth_string = f"user={from_email}\x01auth=Bearer {cfg.oauth_access_token}\x01\x01"
        auth_b64 = base64.b64encode(auth_string.encode()).decode()

        msg_full = MIMEMultipart("alternative")
        msg_full["Subject"]    = subject
        msg_full["From"]       = from_email
        msg_full["To"]         = to_email
        msg_full["Date"]       = formatdate(localtime=False)
        msg_full["Message-ID"] = msg_id
        msg_full.attach(MIMEText(html_body, "html", "utf-8"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            server.docmd("AUTH", f"XOAUTH2 {auth_b64}")
            server.sendmail(from_email, [to_email], msg_full.as_bytes())
    else:
        logger.warning(f"Unsupported OAuth provider '{provider}' for ticket email")
        return None

    logger.info(f"Email sent to {to_email} for {ticket.ticket_id} [{action_label}] via OAuth ({provider})")
    return msg_id
