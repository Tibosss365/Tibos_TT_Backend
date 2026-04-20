import secrets
import urllib.parse
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.admin import AlertSettings, EmailConfig, OAuthProvider, SLAConfig, TicketSettings
from app.models.ticket import Ticket, TicketPriority, TicketStatus
from sqlalchemy import update as sa_update
from app.models.user import User
from app.services.email_sender import send_test_email
from app.schemas.admin import (
    AdminStats,
    AlertSettingsOut,
    AlertSettingsUpdate,
    EmailConfigOut,
    EmailConfigUpdate,
    EmailTestRequest,
    OAuthAuthorizeUrl,
    OAuthCallbackRequest,
    SLAConfigOut,
    SLAConfigUpdate,
    TicketSettingsOut,
    TicketSettingsUpdate,
)

# Provider OAuth endpoint presets
_OAUTH_PRESETS = {
    OAuthProvider.google: {
        "auth_endpoint":  "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    OAuthProvider.microsoft: {
        "auth_endpoint":  "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    },
}

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Ticket Settings ────────────────────────────────────────────────────────

@router.get("/ticket-settings", response_model=TicketSettingsOut)
async def get_ticket_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(TicketSettings).limit(1))
    ts = result.scalar_one_or_none()
    if not ts:
        ts = TicketSettings()
        db.add(ts)
        await db.flush()
        await db.refresh(ts)
    return TicketSettingsOut.model_validate(ts)


@router.put("/ticket-settings", response_model=TicketSettingsOut)
async def update_ticket_settings(
    body: TicketSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(TicketSettings).limit(1))
    ts = result.scalar_one_or_none()
    if not ts:
        ts = TicketSettings()
        db.add(ts)

    ts.number_prefix    = body.number_prefix.strip().upper() or "TKT"
    ts.number_digits    = max(1, min(8, body.number_digits))
    ts.default_status   = body.default_status
    ts.default_priority = body.default_priority

    await db.flush()
    await db.refresh(ts)
    return TicketSettingsOut.model_validate(ts)


# ── SLA ────────────────────────────────────────────────────────────────────

@router.get("/sla", response_model=SLAConfigOut)
async def get_sla(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(SLAConfig))
    sla = result.scalar_one_or_none()
    if not sla:
        # Auto-create default SLA config so the route always returns 200
        sla = SLAConfig(critical_hours=1, high_hours=4, medium_hours=8, low_hours=24)
        db.add(sla)
        await db.flush()
        await db.refresh(sla)
    return SLAConfigOut.model_validate(sla)


@router.put("/sla", response_model=SLAConfigOut)
async def update_sla(
    body: SLAConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from datetime import timedelta

    result = await db.execute(select(SLAConfig))
    sla = result.scalar_one_or_none()
    if not sla:
        sla = SLAConfig()
        db.add(sla)

    sla.critical_hours  = body.critical_hours
    sla.high_hours      = body.high_hours
    sla.medium_hours    = body.medium_hours
    sla.low_hours       = body.low_hours
    sla.timer_start     = body.timer_start
    sla.countdown_mode  = body.countdown_mode
    sla.work_days       = body.work_days
    sla.work_start      = body.work_start
    sla.work_end        = body.work_end
    sla.pause_on        = body.pause_on

    await db.flush()

    # Recalculate sla_due_at for all active (non-closed, non-resolved, non-on-hold) tickets
    active_statuses = [TicketStatus.open, TicketStatus.in_progress]
    hours_by_priority = {
        TicketPriority.critical: body.critical_hours,
        TicketPriority.high:     body.high_hours,
        TicketPriority.medium:   body.medium_hours,
        TicketPriority.low:      body.low_hours,
    }
    for priority, hours in hours_by_priority.items():
        await db.execute(
            sa_update(Ticket)
            .where(Ticket.priority == priority, Ticket.status.in_(active_statuses))
            .values(sla_due_at=Ticket.created_at + timedelta(hours=hours))
        )

    await db.refresh(sla)
    return SLAConfigOut.model_validate(sla)


# ── Email Config ───────────────────────────────────────────────────────────

@router.get("/email", response_model=EmailConfigOut)
async def get_email(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(EmailConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        # Auto-create a default (unconfigured) email config so the route returns 200
        cfg = EmailConfig()
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


@router.put("/email", response_model=EmailConfigOut)
async def update_email(
    body: EmailConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(EmailConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = EmailConfig()
        db.add(cfg)

    cfg.type = body.type
    if body.triggers:
        cfg.trigger_new = body.triggers.trigger_new
        cfg.trigger_assign = body.triggers.trigger_assign
        cfg.trigger_resolve = body.triggers.trigger_resolve

    if body.smtp and body.type.value == "smtp":
        cfg.smtp_host = body.smtp.host
        cfg.smtp_port = body.smtp.port
        cfg.smtp_security = body.smtp.security
        cfg.smtp_from = body.smtp.from_address
        cfg.smtp_user = body.smtp.user
        if body.smtp.password:
            cfg.smtp_pass = body.smtp.password

    if body.m365 and body.type.value == "m365":
        cfg.m365_tenant_id = body.m365.tenant_id
        cfg.m365_client_id = body.m365.client_id
        cfg.m365_from = body.m365.from_address
        if body.m365.client_secret:
            cfg.m365_client_secret = body.m365.client_secret

    if body.oauth and body.type.value == "oauth":
        o = body.oauth
        cfg.oauth_provider = o.provider
        cfg.oauth_client_id = o.client_id
        cfg.oauth_redirect_uri = o.redirect_uri
        cfg.oauth_scopes = o.scopes
        cfg.oauth_from = o.from_address
        if o.client_secret:
            cfg.oauth_client_secret = o.client_secret
        # Use preset endpoints if not custom
        preset = _OAUTH_PRESETS.get(o.provider, {})
        cfg.oauth_auth_endpoint  = o.auth_endpoint  or preset.get("auth_endpoint", "")
        cfg.oauth_token_endpoint = o.token_endpoint or preset.get("token_endpoint", "")

    await db.flush()
    await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


# ── OAuth 2.0 Flow ─────────────────────────────────────────────────────────

@router.get("/email/oauth/authorize", response_model=OAuthAuthorizeUrl)
async def oauth_get_authorize_url(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Build the OAuth authorization URL to redirect the user to."""
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()
    if not cfg or not cfg.oauth_client_id or not cfg.oauth_auth_endpoint:
        raise HTTPException(status_code=400, detail="OAuth not configured — save credentials first")

    state = secrets.token_urlsafe(16)
    params = {
        "client_id":     cfg.oauth_client_id,
        "redirect_uri":  cfg.oauth_redirect_uri or "",
        "response_type": "code",
        "scope":         cfg.oauth_scopes or "",
        "access_type":   "offline",  # Google: request refresh token
        "prompt":        "consent",
        "state":         state,
    }
    url = cfg.oauth_auth_endpoint + "?" + urllib.parse.urlencode(params)
    return OAuthAuthorizeUrl(url=url, state=state)


@router.post("/email/oauth/callback", response_model=EmailConfigOut)
async def oauth_callback(
    body: OAuthCallbackRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Exchange authorization code for access + refresh tokens."""
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()
    if not cfg or not cfg.oauth_client_id or not cfg.oauth_token_endpoint:
        raise HTTPException(status_code=400, detail="OAuth not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg.oauth_token_endpoint,
            data={
                "code":          body.code,
                "client_id":     cfg.oauth_client_id,
                "client_secret": cfg.oauth_client_secret or "",
                "redirect_uri":  cfg.oauth_redirect_uri or "",
                "grant_type":    "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    expires_in = token_data.get("expires_in", 3600)
    cfg.oauth_access_token  = token_data.get("access_token")
    cfg.oauth_refresh_token = token_data.get("refresh_token")
    cfg.oauth_token_expiry  = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc
    )
    await db.flush()
    await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


@router.delete("/email/oauth/revoke", response_model=EmailConfigOut)
async def oauth_revoke(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Revoke stored OAuth tokens."""
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Email config not found")

    cfg.oauth_access_token  = None
    cfg.oauth_refresh_token = None
    cfg.oauth_token_expiry  = None
    await db.flush()
    await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


@router.post("/email/test")
async def test_email(
    body: EmailTestRequest,
    _: User = Depends(require_admin),
):
    """
    Send a test email using the credentials provided in the request body.
    Credentials come directly from the frontend form — DB is NOT read.
    This lets the user test before saving.
    Works for SMTP, M365 (Microsoft Graph), and OAuth 2.0.
    """
    from app.services.email_sender import send_test_from_request
    try:
        await send_test_from_request(body)
        return {"ok": True, "message": f"Test email sent successfully to {body.to_email}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


# ── Stats / Overview ───────────────────────────────────────────────────────

@router.get("/stats", response_model=AdminStats)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    # Count by status
    status_counts: dict[str, int] = {}
    for s in TicketStatus:
        res = await db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.status == s)
        )
        status_counts[s.value] = res.scalar_one()

    critical_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.priority == TicketPriority.critical,
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )
    critical = critical_res.scalar_one()

    unassigned_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.assignee_id.is_(None),
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )
    unassigned = unassigned_res.scalar_one()

    # Agent workload
    agents_res = await db.execute(select(User).where(User.is_active == True))
    agents = agents_res.scalars().all()
    agent_workload = []
    for agent in agents:
        open_res = await db.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.assignee_id == agent.id,
                Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
            )
        )
        agent_workload.append({
            "id": str(agent.id),
            "name": agent.name,
            "initials": agent.initials,
            "group": agent.group,
            "open_tickets": open_res.scalar_one(),
        })

    total = sum(status_counts.values())
    return AdminStats(
        total_tickets=total,
        open_tickets=status_counts.get("open", 0),
        in_progress_tickets=status_counts.get("in-progress", 0),
        resolved_tickets=status_counts.get("resolved", 0),
        closed_tickets=status_counts.get("closed", 0),
        critical_tickets=critical,
        unassigned_tickets=unassigned,
        agent_workload=agent_workload,
    )


# ── Alert Settings ─────────────────────────────────────────────────────────

async def _get_or_create_alert_settings(db: AsyncSession) -> AlertSettings:
    result = await db.execute(select(AlertSettings).limit(1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = AlertSettings()
        db.add(settings)
        await db.flush()
        await db.refresh(settings)
    return settings


@router.get("/alerts", response_model=AlertSettingsOut)
async def get_alert_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    settings = await _get_or_create_alert_settings(db)
    return AlertSettingsOut.model_validate(settings)


@router.put("/alerts", response_model=AlertSettingsOut)
async def update_alert_settings(
    body: AlertSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    settings = await _get_or_create_alert_settings(db)
    settings.conditions = body.conditions
    settings.reports    = body.reports
    settings.recipients = body.recipients
    await db.flush()
    await db.refresh(settings)
    return AlertSettingsOut.model_validate(settings)


@router.post("/alerts/test")
async def test_alert(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Send an immediate alert summary email to all configured recipients.
    Queries the live ticket state and sends via the stored email config.
    """
    from datetime import timezone as _tz

    # ── 1. Load alert settings ────────────────────────────────────────────
    alert_cfg = await _get_or_create_alert_settings(db)
    recipients_cfg: dict = alert_cfg.recipients or {}

    # Build recipient list
    to_emails: list[str] = list(recipients_cfg.get("emails") or [])
    if recipients_cfg.get("includeAdmin", True):
        if current_user.email:
            to_emails.insert(0, current_user.email)

    if not to_emails:
        raise HTTPException(
            status_code=400,
            detail="No recipients configured — add at least one email address or enable Admin Account.",
        )

    # ── 2. Load email config ──────────────────────────────────────────────
    email_result = await db.execute(select(EmailConfig))
    email_cfg: EmailConfig | None = email_result.scalar_one_or_none()
    if not email_cfg:
        raise HTTPException(
            status_code=400,
            detail="Email is not configured — set up SMTP / M365 in the Email tab first.",
        )

    email_type = email_cfg.type.value if email_cfg.type else "smtp"
    from_addr = (
        (email_cfg.smtp_from or email_cfg.smtp_user or "")   if email_type == "smtp"
        else (email_cfg.m365_from or "")                      if email_type == "m365"
        else (email_cfg.oauth_from or "")
    )
    if not from_addr:
        raise HTTPException(
            status_code=400,
            detail="Email sender address is not set — configure it in the Email tab.",
        )

    # ── 3. Gather live ticket stats ───────────────────────────────────────
    now = datetime.now(_tz.utc)
    active_statuses = [TicketStatus.open, TicketStatus.in_progress, TicketStatus.on_hold]

    # Unassigned
    unassigned_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.assignee_id.is_(None),
            Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress]),
        )
    )
    unassigned_count = unassigned_res.scalar_one()

    # SLA breached (sla_due_at in the past, ticket still active)
    sla_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.sla_due_at.isnot(None),
            Ticket.sla_due_at < now,
            Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress]),
        )
    )
    sla_breach_count = sla_res.scalar_one()

    # On-hold
    on_hold_res = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.on_hold)
    )
    on_hold_count = on_hold_res.scalar_one()

    # Open today (created since midnight UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    open_today_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.created_at >= midnight,
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )
    open_today_count = open_today_res.scalar_one()

    # ── 4. Build alert email HTML ─────────────────────────────────────────
    date_str = now.strftime("%B %d, %Y at %H:%M UTC")

    def _stat_row(color: str, emoji: str, label: str, count: int, note: str = "") -> str:
        bg = {"red": "#fef2f2", "amber": "#fffbeb", "violet": "#f5f3ff", "blue": "#eff6ff"}.get(color, "#f9fafb")
        bd = {"red": "#fecaca", "amber": "#fde68a", "violet": "#ddd6fe", "blue": "#bfdbfe"}.get(color, "#e5e7eb")
        tx = {"red": "#dc2626", "amber": "#d97706", "violet": "#7c3aed", "blue": "#2563eb"}.get(color, "#374151")
        return f"""
        <tr>
          <td style="padding:10px 24px;border-bottom:1px solid #f3f4f6;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:40px;height:40px;background:{bg};border:1px solid {bd};
                            border-radius:10px;text-align:center;vertical-align:middle;font-size:18px;">{emoji}</td>
                <td style="padding-left:14px;vertical-align:middle;">
                  <div style="font-size:13px;font-weight:600;color:#374151;">{label}</div>
                  {f'<div style="font-size:11px;color:#9ca3af;margin-top:1px;">{note}</div>' if note else ''}
                </td>
                <td style="text-align:right;vertical-align:middle;">
                  <span style="font-size:22px;font-weight:800;color:{tx};">{count}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    stats_rows = (
        _stat_row("amber", "👤", "Unassigned Tickets",  unassigned_count,  "No agent assigned yet") +
        _stat_row("red",   "⚠️",  "SLA Breaches",        sla_breach_count,  "Past response deadline") +
        _stat_row("violet","⏸",  "On-Hold Tickets",     on_hold_count,     "Awaiting action") +
        _stat_row("blue",  "📬", "Opened Today",        open_today_count,  f"Since midnight UTC")
    )

    html_body = f"""
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">
        <tr><td align="center">
          <table width="560" cellpadding="0" cellspacing="0"
                 style="background:#fff;border-radius:16px;overflow:hidden;
                        box-shadow:0 4px 24px rgba(0,0,0,.08);">

            <!-- Header -->
            <tr><td style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:28px 24px;">
              <table width="100%" cellpadding="0" cellspacing="0"><tr>
                <td>
                  <div style="font-size:11px;font-weight:700;color:rgba(255,255,255,.7);
                               text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">
                    🔔 Alert Summary
                  </div>
                  <div style="font-size:22px;font-weight:800;color:#fff;">Ticket Health Report</div>
                  <div style="font-size:12px;color:rgba(255,255,255,.7);margin-top:4px;">{date_str}</div>
                </td>
                <td style="text-align:right;font-size:40px;">📊</td>
              </tr></table>
            </td></tr>

            <!-- Stats -->
            <tr><td style="padding:20px 0 8px;">
              <div style="font-size:11px;font-weight:700;color:#9ca3af;
                           text-transform:uppercase;letter-spacing:.8px;
                           padding:0 24px 12px;">Current Status</div>
              <table width="100%" cellpadding="0" cellspacing="0">{stats_rows}</table>
            </td></tr>

            <!-- Footer -->
            <tr><td style="padding:20px 24px 28px;border-top:1px solid #f3f4f6;">
              <p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">
                This is a test alert sent from your Tibos Helpdesk admin panel.<br>
                Manage alert settings in <strong>Admin → Alerts</strong>.
              </p>
            </td></tr>

          </table>
        </td></tr>
      </table>
    </body></html>
    """

    # ── 5. Send to all recipients ─────────────────────────────────────────
    subject = f"🔔 [Helpdesk Alert] Ticket Summary — {now.strftime('%b %d, %Y')}"
    errors: list[str] = []

    for to_email in to_emails:
        try:
            await _send_alert_email(email_cfg, from_addr, to_email, subject, html_body)
        except Exception as exc:
            logger.warning(f"Alert email to {to_email} failed: {exc}")
            errors.append(str(exc))

    if errors and len(errors) == len(to_emails):
        # All sends failed
        raise HTTPException(status_code=502, detail=f"All alert sends failed: {errors[0]}")

    sent = len(to_emails) - len(errors)
    return {"ok": True, "sent": sent, "recipients": to_emails}


# ── Shared alert email sender ──────────────────────────────────────────────

import logging as _logging
_logger = _logging.getLogger(__name__)

import smtplib as _smtplib
import ssl as _ssl
from email.mime.multipart import MIMEMultipart as _MIMEMultipart
from email.mime.text import MIMEText as _MIMEText
from email.utils import formatdate as _formatdate, make_msgid as _make_msgid


async def _send_alert_email(
    cfg: EmailConfig,
    from_addr: str,
    to_email: str,
    subject: str,
    html_body: str,
) -> None:
    """Send a plain HTML alert email using the stored EmailConfig."""
    email_type = cfg.type.value if cfg.type else "smtp"

    if email_type == "smtp":
        if not cfg.smtp_host:
            raise RuntimeError("SMTP host not configured")
        msg = _MIMEMultipart("alternative")
        msg["Subject"]    = subject
        msg["From"]       = from_addr
        msg["To"]         = to_email
        msg["Date"]       = _formatdate(localtime=False)
        msg["Message-ID"] = _make_msgid(domain=(from_addr.split("@")[-1] if "@" in from_addr else "helpdesk"))
        msg.attach(_MIMEText(html_body, "html", "utf-8"))

        port = int(cfg.smtp_port or 587)
        host = cfg.smtp_host

        from app.models.admin import SMTPSecurity
        if cfg.smtp_security == SMTPSecurity.ssl:
            ctx = _ssl.create_default_context()
            with _smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as srv:
                srv.login(cfg.smtp_user or "", cfg.smtp_pass or "")
                srv.sendmail(from_addr, [to_email], msg.as_bytes())
        else:
            with _smtplib.SMTP(host, port, timeout=15) as srv:
                srv.ehlo()
                if cfg.smtp_security == SMTPSecurity.tls:
                    srv.starttls(context=_ssl.create_default_context())
                    srv.ehlo()
                srv.login(cfg.smtp_user or "", cfg.smtp_pass or "")
                srv.sendmail(from_addr, [to_email], msg.as_bytes())

    elif email_type == "m365":
        from app.services.email_sender import _get_graph_token, _send_via_graph
        token = await _get_graph_token(
            cfg.m365_tenant_id or "", cfg.m365_client_id or "", cfg.m365_client_secret or ""
        )
        await _send_via_graph(
            token=token, from_email=from_addr, to_email=to_email,
            subject=subject, html_body=html_body,
        )

    elif email_type == "oauth":
        from app.services.email_sender import _send_via_graph
        provider = (cfg.oauth_provider.value if cfg.oauth_provider else "").lower()
        if provider in ("microsoft", ""):
            await _send_via_graph(
                token=cfg.oauth_access_token or "",
                from_email=from_addr, to_email=to_email,
                subject=subject, html_body=html_body,
            )
        else:
            raise RuntimeError(f"OAuth provider '{provider}' not supported for alert emails yet")
    else:
        raise RuntimeError(f"Unknown email type '{email_type}'")
