import asyncio
import logging
import secrets
import smtplib
import ssl
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, case as sa_case
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

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


@router.get("/alerts", response_model=AlertSettingsOut, response_model_by_alias=True)
async def get_alert_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    settings = await _get_or_create_alert_settings(db)
    return AlertSettingsOut.model_validate(settings)


@router.put("/alerts", response_model=AlertSettingsOut, response_model_by_alias=True)
async def update_alert_settings(
    body: AlertSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    settings = await _get_or_create_alert_settings(db)
    settings.conditions         = body.conditions
    settings.reports            = body.reports
    settings.recipients         = body.recipients
    settings.alert_email_config = body.alert_email_config
    await db.flush()
    await db.refresh(settings)
    return AlertSettingsOut.model_validate(settings)


@router.post("/alerts/test")
async def test_alert(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Send an immediate alert-summary email to all configured recipients.
    All blocking I/O (SMTP) runs in a thread-pool executor so the async
    event loop is never blocked.  A top-level try/except guarantees that
    the client always receives a proper HTTP response (never a bare drop).
    """
    try:
        return await _run_test_alert(db, current_user)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error in test_alert: %s", exc)
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")


async def _run_test_alert(db: AsyncSession, current_user: User) -> dict:
    """Inner implementation — separated so the outer handler can catch everything."""

    # ── 1. Load alert settings ────────────────────────────────────────────
    alert_cfg = await _get_or_create_alert_settings(db)
    recipients_cfg: dict = alert_cfg.recipients or {}

    # Build recipient list — username is used as email if it looks like one
    to_emails: list[str] = list(recipients_cfg.get("emails") or [])
    if recipients_cfg.get("includeAdmin", True):
        admin_email = current_user.username if "@" in (current_user.username or "") else None
        if admin_email and admin_email not in to_emails:
            to_emails.insert(0, admin_email)

    if not to_emails:
        raise HTTPException(
            status_code=400,
            detail=(
                "No recipients configured. "
                "Add custom email addresses in Alerts → Recipients, "
                "or make sure the Admin username is a valid email address."
            ),
        )

    # ── 2. Resolve email credentials ─────────────────────────────────────
    alert_email_cfg: dict = (alert_cfg.alert_email_config or {})
    use_same = alert_email_cfg.get("useSameAsEmail", True)
    send_params: dict  # will be passed to _send_smtp_alert or _send_graph_alert

    if use_same:
        email_result = await db.execute(select(EmailConfig))
        sys_cfg: EmailConfig | None = email_result.scalar_one_or_none()
        if not sys_cfg:
            raise HTTPException(
                status_code=400,
                detail="Email is not configured — set up SMTP / M365 in the Email tab first.",
            )
        email_type = sys_cfg.type.value if sys_cfg.type else "smtp"
        if email_type == "smtp":
            from_addr = sys_cfg.smtp_from or sys_cfg.smtp_user or ""
            if not sys_cfg.smtp_host or not from_addr:
                raise HTTPException(
                    status_code=400,
                    detail="SMTP is not fully configured — fill in Host and From Address in the Email tab.",
                )
            send_params = {
                "method": "smtp",
                "host": sys_cfg.smtp_host,
                "port": int(sys_cfg.smtp_port or 587),
                "security": (sys_cfg.smtp_security.value if sys_cfg.smtp_security else "tls"),
                "user": sys_cfg.smtp_user or "",
                "password": sys_cfg.smtp_pass or "",
                "from_addr": from_addr,
            }
        elif email_type == "m365":
            from_addr = sys_cfg.m365_from or ""
            if not sys_cfg.m365_tenant_id or not sys_cfg.m365_client_id or not from_addr:
                raise HTTPException(
                    status_code=400,
                    detail="M365 is not fully configured — fill in Tenant ID, Client ID, and From Address.",
                )
            send_params = {
                "method": "m365",
                "tenant_id": sys_cfg.m365_tenant_id,
                "client_id": sys_cfg.m365_client_id,
                "client_secret": sys_cfg.m365_client_secret or "",
                "from_addr": from_addr,
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Email type '{email_type}' is not supported for alert emails. Use SMTP or M365.",
            )
    else:
        # Dedicated alert email config
        atype = alert_email_cfg.get("type", "smtp")
        if atype == "smtp":
            sc = alert_email_cfg.get("smtp") or {}
            from_addr = sc.get("from") or sc.get("user") or ""
            if not sc.get("host") or not from_addr:
                raise HTTPException(
                    status_code=400,
                    detail="Alert SMTP is incomplete — fill in Host and From Address in Alerts → Alert Email Account.",
                )
            send_params = {
                "method": "smtp",
                "host": sc.get("host", ""),
                "port": int(sc.get("port") or 587),
                "security": sc.get("security", "tls"),
                "user": sc.get("user", ""),
                "password": sc.get("pass", ""),
                "from_addr": from_addr,
            }
        elif atype == "m365":
            mc = alert_email_cfg.get("m365") or {}
            from_addr = mc.get("from", "")
            if not mc.get("tenantId") or not mc.get("clientId") or not from_addr:
                raise HTTPException(
                    status_code=400,
                    detail="Alert M365 is incomplete — fill in Tenant ID, Client ID, and From Address.",
                )
            send_params = {
                "method": "m365",
                "tenant_id": mc.get("tenantId", ""),
                "client_id": mc.get("clientId", ""),
                "client_secret": mc.get("clientSecret", ""),
                "from_addr": from_addr,
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported alert email type: '{atype}'")

    # ── 3. Gather live ticket counts ──────────────────────────────────────
    now = datetime.now(timezone.utc)

    unassigned_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.assignee_id.is_(None),
            Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress]),
        )
    )
    sla_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.sla_due_at.isnot(None),
            Ticket.sla_due_at < now,
            Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress]),
        )
    )
    on_hold_res = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.on_hold)
    )
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    open_today_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.created_at >= midnight,
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )
    created_today_res = await db.execute(
        select(func.count()).select_from(Ticket).where(Ticket.created_at >= midnight)
    )
    resolved_today_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.updated_at >= midnight,
            Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )

    # Agent stats — all active users, merged with per-status ticket counts
    # Step 1: fetch every active user
    all_users_res = await db.execute(select(User).where(User.is_active == True))
    all_users = all_users_res.scalars().all()

    # Step 2: per-assignee ticket counts via a single GROUP BY query
    ticket_counts_q = await db.execute(
        select(
            Ticket.assignee_id,
            func.count().label("total"),
            func.sum(sa_case((Ticket.status == TicketStatus.open,        1), else_=0)).label("open"),
            func.sum(sa_case((Ticket.status == TicketStatus.in_progress, 1), else_=0)).label("in_progress"),
            func.sum(sa_case((Ticket.status == TicketStatus.on_hold,     1), else_=0)).label("on_hold"),
            func.sum(sa_case((Ticket.status == TicketStatus.resolved,    1), else_=0)).label("resolved"),
            func.sum(sa_case((Ticket.status == TicketStatus.closed,      1), else_=0)).label("closed"),
        ).where(
            Ticket.assignee_id.isnot(None)
        ).group_by(Ticket.assignee_id)
    )
    ticket_rows = {str(r.assignee_id): r for r in ticket_counts_q.fetchall()}

    # Step 3: merge — every user gets a row (zeros if no tickets)
    agent_stats = sorted([
        {
            "name":        u.name or u.username,
            "initials":    (u.initials or "".join(w[0].upper() for w in (u.name or u.username or "?").split()[:2]) or "?"),
            "total":       int((ticket_rows[str(u.id)].total       if str(u.id) in ticket_rows else 0) or 0),
            "open":        int((ticket_rows[str(u.id)].open        if str(u.id) in ticket_rows else 0) or 0),
            "in_progress": int((ticket_rows[str(u.id)].in_progress if str(u.id) in ticket_rows else 0) or 0),
            "on_hold":     int((ticket_rows[str(u.id)].on_hold     if str(u.id) in ticket_rows else 0) or 0),
            "resolved":    int(((ticket_rows[str(u.id)].resolved if str(u.id) in ticket_rows else 0) or 0)
                             + ((ticket_rows[str(u.id)].closed   if str(u.id) in ticket_rows else 0) or 0)),
        }
        for u in all_users
    ], key=lambda x: x["total"], reverse=True)

    counts = {
        "unassigned":    unassigned_res.scalar_one(),
        "sla_breach":    sla_res.scalar_one(),
        "on_hold":       on_hold_res.scalar_one(),
        "open_today":    open_today_res.scalar_one(),
        "created_today": created_today_res.scalar_one(),
        "resolved_today": resolved_today_res.scalar_one(),
    }

    # ── 4. Build HTML email ───────────────────────────────────────────────
    subject = "Alert Summary: Ticket Health Report — " + now.strftime("%b %d, %Y")
    html_body = _build_alert_html(counts, now, agent_stats=agent_stats)

    # ── 5. Send (non-blocking) ────────────────────────────────────────────
    errors: list[str] = []
    for to_email in to_emails:
        try:
            await _dispatch_alert_email(send_params, to_email, subject, html_body)
        except Exception as exc:
            logger.warning("Alert email to %s failed: %s", to_email, exc)
            errors.append(f"{to_email}: {exc}")

    if errors and len(errors) == len(to_emails):
        raise HTTPException(status_code=502, detail=f"Email delivery failed — {errors[0]}")

    return {"ok": True, "sent": len(to_emails) - len(errors), "recipients": to_emails}


# ── HTML builder ───────────────────────────────────────────────────────────

def _build_alert_html(
    counts: dict,
    now: datetime,
    agent_stats: list | None = None,
    template: dict | None = None,
) -> str:
    """
    Build the full alert/report HTML email.

    `template` mirrors the frontend template object (includeUnassigned, includeSla, …).
    When None (e.g. for the test-send endpoint), every section is shown.
    """
    def _show(key: str) -> bool:
        """Return True if the section should be included."""
        if template is None:
            return True
        return template.get(key, True) is not False

    date_str = now.strftime("%B %d, %Y at %H:%M UTC")

    # ── Stat rows (ticket counts) ─────────────────────────────────────────
    _STAT_DEFS = [
        ("amber",   "&#128100;", "Unassigned Tickets",  "unassigned",    "includeUnassigned",    "No agent assigned yet"),
        ("red",     "&#9888;",   "SLA Breaches",         "sla_breach",    "includeSla",           "Past response deadline"),
        ("violet",  "&#9208;",   "On-Hold Tickets",      "on_hold",       "includeOnHold",        "Awaiting action"),
        ("blue",    "&#128236;", "Currently Open",       "open_today",    "includeOpenToday",     "Active tickets right now"),
        ("emerald", "&#128229;", "Created Today",        "created_today", "includeCreatedToday",  "New tickets since midnight"),
        ("green",   "&#9989;",   "Resolved Today",       "resolved_today","includeResolvedToday", "Closed since midnight"),
    ]
    _BG = {"red":"#fef2f2","amber":"#fffbeb","violet":"#f5f3ff","blue":"#eff6ff",
           "emerald":"#ecfdf5","green":"#f0fdf4"}
    _BD = {"red":"#fecaca","amber":"#fde68a","violet":"#ddd6fe","blue":"#bfdbfe",
           "emerald":"#a7f3d0","green":"#bbf7d0"}
    _TX = {"red":"#dc2626","amber":"#d97706","violet":"#7c3aed","blue":"#2563eb",
           "emerald":"#059669","green":"#16a34a"}

    stat_rows = ""
    for color, emoji, label, count_key, tmpl_key, note in _STAT_DEFS:
        if not _show(tmpl_key):
            continue
        n = counts.get(count_key, 0)
        stat_rows += (
            f'<tr><td style="padding:10px 24px;border-bottom:1px solid #f3f4f6;">'
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="width:40px;height:40px;background:{_BG[color]};border:1px solid {_BD[color]};'
            f'border-radius:10px;text-align:center;vertical-align:middle;font-size:18px;">{emoji}</td>'
            f'<td style="padding-left:14px;vertical-align:middle;">'
            f'<div style="font-size:13px;font-weight:600;color:#374151;">{label}</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:1px;">{note}</div>'
            f'</td><td style="text-align:right;vertical-align:middle;">'
            f'<span style="font-size:22px;font-weight:800;color:{_TX[color]};">{n}</span>'
            f'</td></tr></table></td></tr>'
        )

    # ── Agent Status Table ────────────────────────────────────────────────
    agent_section = ""
    if _show("includeAgentStats") and agent_stats is not None:
        # Colour palette for avatar circles (cycles through list)
        _AVATAR_COLORS = ["#4f46e5","#0891b2","#059669","#d97706","#dc2626","#7c3aed","#0284c7","#16a34a"]
        header_cells = "".join(
            f'<th style="padding:10px 14px;text-align:{align};font-size:10px;font-weight:700;'
            f'color:#6b7280;text-transform:uppercase;letter-spacing:.6px;border-bottom:1px solid #e5e7eb;">{h}</th>'
            for h, align in [
                ("Agent", "left"), ("Total", "right"), ("Open", "right"),
                ("In Progress", "right"), ("On Hold", "right"), ("Resolved", "right"),
            ]
        )
        agent_body = ""
        for i, ag in enumerate(agent_stats):
            row_bg     = "#f9fafb" if i % 2 == 0 else "#ffffff"
            avatar_bg  = _AVATAR_COLORS[i % len(_AVATAR_COLORS)]
            initials   = ag.get("initials") or "?"
            total      = ag["total"]
            open_c     = ag["open"]
            inp_c      = ag["in_progress"]
            hold_c     = ag["on_hold"]
            res_c      = ag["resolved"]

            def _badge(val: int, color: str) -> str:
                """Coloured number — grey when zero."""
                c = color if val > 0 else "#d1d5db"
                return (f'<span style="font-size:13px;font-weight:{"700" if val>0 else "500"};'
                        f'color:{c};">{val}</span>')

            agent_body += (
                f'<tr style="background:{row_bg};">'
                f'<td style="padding:10px 14px;">'
                f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;"><tr>'
                f'<td style="width:30px;height:30px;min-width:30px;background:{avatar_bg};border-radius:50%;'
                f'text-align:center;vertical-align:middle;font-size:11px;font-weight:700;color:#fff;">{initials}</td>'
                f'<td style="padding-left:10px;font-size:13px;font-weight:600;color:#374151;white-space:nowrap;">{ag["name"]}</td>'
                f'</tr></table></td>'
                f'<td style="padding:10px 14px;text-align:right;">'
                f'<span style="font-size:13px;font-weight:700;color:#111827;">{total}</span></td>'
                f'<td style="padding:10px 14px;text-align:right;">{_badge(open_c,  "#2563eb")}</td>'
                f'<td style="padding:10px 14px;text-align:right;">{_badge(inp_c,   "#7c3aed")}</td>'
                f'<td style="padding:10px 14px;text-align:right;">{_badge(hold_c,  "#d97706")}</td>'
                f'<td style="padding:10px 14px;text-align:right;">{_badge(res_c,   "#059669")}</td>'
                f'</tr>'
            )
        agent_section = (
            '<tr><td style="padding:24px 24px 8px;">'
            '<div style="font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;'
            'letter-spacing:.8px;margin-bottom:12px;">&#128100;&nbsp; Agent Wise Ticket Count</div>'
            '<table width="100%" cellpadding="0" cellspacing="0" '
            'style="border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;border-collapse:separate;border-spacing:0;">'
            f'<thead style="background:#f9fafb;"><tr>{header_cells}</tr></thead>'
            f'<tbody>{agent_body}</tbody>'
            '</table>'
            '</td></tr>'
        )

    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background:#f3f4f6;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 16px;">'
        '<tr><td align="center">'
        '<table width="600" cellpadding="0" cellspacing="0" '
        'style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">'
        '<tr><td style="background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:28px 24px;">'
        '<div style="font-size:11px;font-weight:700;color:rgba(255,255,255,.7);'
        'text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">&#128276; Alert Summary</div>'
        f'<div style="font-size:22px;font-weight:800;color:#fff;">Ticket Health Report</div>'
        f'<div style="font-size:12px;color:rgba(255,255,255,.7);margin-top:4px;">{date_str}</div>'
        '</td></tr>'
        + (
            '<tr><td style="padding:20px 0 8px;">'
            '<div style="font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;'
            f'letter-spacing:.8px;padding:0 24px 12px;">Current Status</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0">{stat_rows}</table>'
            '</td></tr>'
            if stat_rows else ""
        )
        + agent_section
        + '<tr><td style="padding:20px 24px 28px;border-top:1px solid #f3f4f6;">'
        '<p style="margin:0;font-size:12px;color:#9ca3af;text-align:center;">'
        'This is a test alert sent from your Tibos Helpdesk admin panel.<br>'
        'Manage alert settings in <strong>Admin &rarr; Alerts</strong>.</p>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )


# ── Non-blocking email dispatcher ─────────────────────────────────────────

async def _dispatch_alert_email(
    params: dict, to_email: str, subject: str, html_body: str
) -> None:
    """
    Route to the correct sending method.
    SMTP runs in a thread-pool executor (run_in_executor) so the async
    event loop is never blocked by synchronous socket I/O.
    M365 / Graph API uses httpx (native async).
    """
    method = params.get("method", "smtp")

    if method == "smtp":
        from_addr = params["from_addr"]
        host      = params["host"]
        port      = params["port"]
        security  = params.get("security", "tls")
        user      = params.get("user", "")
        password  = params.get("password", "")

        # Build the MIME message synchronously (cheap, no I/O)
        msg = MIMEMultipart("alternative")
        msg["Subject"]    = subject
        msg["From"]       = from_addr
        msg["To"]         = to_email
        msg["Date"]       = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=(from_addr.split("@")[-1] if "@" in from_addr else "helpdesk"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        raw = msg.as_bytes()

        def _smtp_send() -> None:
            if security == "ssl":
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as srv:
                    srv.login(user, password)
                    srv.sendmail(from_addr, [to_email], raw)
            else:
                with smtplib.SMTP(host, port, timeout=20) as srv:
                    srv.ehlo()
                    if security == "tls":
                        srv.starttls(context=ssl.create_default_context())
                        srv.ehlo()
                    srv.login(user, password)
                    srv.sendmail(from_addr, [to_email], raw)

        # Run blocking SMTP in a thread so the event loop stays free
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _smtp_send)

    elif method == "m365":
        from app.services.email_sender import _get_graph_token, _send_via_graph
        token = await _get_graph_token(
            params["tenant_id"], params["client_id"], params["client_secret"]
        )
        await _send_via_graph(
            token=token,
            from_email=params["from_addr"],
            to_email=to_email,
            subject=subject,
            html_body=html_body,
        )
    else:
        raise RuntimeError(f"Unsupported send method: {method}")
