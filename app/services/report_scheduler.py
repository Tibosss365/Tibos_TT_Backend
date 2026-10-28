"""
Report Scheduler
================
Background asyncio service that sends scheduled digest reports.

Checks every 60 s whether a daily / weekly / monthly report is due,
then builds and sends the HTML email to all configured recipients.

Schedule logic
--------------
- Daily   : send at the configured ``time`` every day
- Weekly  : send at the configured ``time`` on the configured ``day``
- Monthly : send at the configured ``time`` on ``dayOfMonth``
            (clamped to the last day of the month for short months)

Timezone
--------
Resolved in priority order: ``reports["timezone"]`` key in AlertSettings →
``email_config.trigger_timezone`` → "Asia/Kolkata" (IST, default fallback).
All configured HH:MM times (daily/weekly/monthly) are in that timezone.

"Due" definition
----------------
A report is due when:
  1. The current UTC time >= the scheduled HH:MM today
  2. It has NOT already been sent today (tracked in ``alert_settings.last_reports_sent``)

This means a report that was missed (server was down at the scheduled time)
is sent on the first scheduler tick after the server restarts, so no digest
is permanently skipped.
"""

import asyncio
import calendar
import logging
import smtplib
import ssl
import zoneinfo
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from sqlalchemy import func, select
from sqlalchemy import case as sa_case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.admin import AlertSettings, EmailConfig
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User
from app.services.report_sections import gather_report_sections, report_subject

logger = logging.getLogger(__name__)

_DAY_NAME_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


class ReportScheduler:
    """
    Background asyncio task — follows the same start/stop pattern as
    SLABreachDetector.  Registered in main.py lifespan.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(
                self._loop(), name="report-scheduler"
            )
            logger.info("Report scheduler started (checks every 60 s)")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Report scheduler stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    # ── Main loop ──────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        # Brief pause on startup so migrations / seeding finish first
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._check_and_send()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Report scheduler error: %s", exc, exc_info=True)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    # ── Schedule check ─────────────────────────────────────────────────────

    async def _check_and_send(self) -> None:
        now_utc = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            result     = await db.execute(select(AlertSettings))
            alert_cfg: AlertSettings | None = result.scalar_one_or_none()
            if not alert_cfg:
                return

            email_res = await db.execute(select(EmailConfig))
            email_cfg: EmailConfig | None = email_res.scalar_one_or_none()

            reports   = alert_cfg.reports or {}

            # Timezone priority: reports dict > EmailConfig trigger_timezone > "Asia/Kolkata"
            tz_name = (
                reports.get("timezone")
                or (email_cfg.trigger_timezone if email_cfg and getattr(email_cfg, "trigger_timezone", None) else None)
                or "Asia/Kolkata"
            )

            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                # tzdata may be missing on the host — try IST, then fall back to
                # UTC so a missing timezone database can never stall reports.
                try:
                    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                except Exception:
                    logger.warning("Report scheduler: timezone data unavailable (%r) — using UTC", tz_name)
                    tz = timezone.utc

            now_tz = now_utc.astimezone(tz)
            today = now_tz.date()
            last_sent = dict(alert_cfg.last_reports_sent or {})
            changed   = False

            for report_type in ("daily", "weekly", "monthly"):  # Existing schedules remain unchanged.
                rep = reports.get(report_type) or {}
                if not rep.get("enabled", False):
                    continue
                if not _is_due(report_type, rep, last_sent, now_tz, today):
                    continue

                logger.info("Report scheduler: %s report is due — sending…", report_type)
                try:
                    await self._send_report(db, alert_cfg, report_type, rep, now_tz)
                    last_sent[report_type] = now_utc.isoformat()
                    changed = True
                    logger.info(
                        "Report scheduler: %s report sent at %s",
                        report_type, now_utc.isoformat(),
                    )
                except Exception as exc:
                    logger.error(
                        "Report scheduler: %s report failed — %s",
                        report_type, exc, exc_info=True,
                    )

            # Separate combined digest. This has its own schedule and dedupe
            # key, so it never replaces or suppresses the existing reports.
            combined_rep = reports.get("dailyMonthly") or {
                "enabled": True,
                "time": "17:45",
            }
            if combined_rep.get("enabled", True) and _is_due(
                "dailyMonthly", combined_rep, last_sent, now_tz, today
            ):
                logger.info("Report scheduler: dailyMonthly report is due — sending…")
                try:
                    await self._send_daily_monthly_report(
                        db, alert_cfg, combined_rep, now_tz
                    )
                    last_sent["dailyMonthly"] = now_utc.isoformat()
                    changed = True
                    logger.info(
                        "Report scheduler: dailyMonthly report sent at %s",
                        now_utc.isoformat(),
                    )
                except Exception as exc:
                    logger.error(
                        "Report scheduler: dailyMonthly report failed — %s",
                        exc, exc_info=True,
                    )

            if changed:
                alert_cfg.last_reports_sent = last_sent
                await db.commit()

    # ── Report builder + sender ────────────────────────────────────────────

    async def _send_report(
        self,
        db: AsyncSession,
        alert_cfg: AlertSettings,
        report_type: str,
        rep: dict,
        now_tz: datetime,
    ) -> None:
        template = rep.get("template") or {}

        # ── Recipients ────────────────────────────────────────────────────
        recipients_cfg = alert_cfg.recipients or {}
        to_emails: list[str] = list(recipients_cfg.get("emails") or [])

        if recipients_cfg.get("includeAdmin", True):
            admin_res = await db.execute(
                select(User).where(User.role == "admin", User.is_active == True)
            )
            for admin_user in admin_res.scalars().all():
                addr = admin_user.username if "@" in (admin_user.username or "") else None
                if addr and addr not in to_emails:
                    to_emails.append(addr)

        if not to_emails:
            logger.warning(
                "Report scheduler: %s report skipped — no recipients configured",
                report_type,
            )
            return

        # ── Email credentials ─────────────────────────────────────────────
        send_params = await _resolve_email_params(db, alert_cfg)

        # ── Ticket data for the correct period ────────────────────────────
        period_start_utc = _period_start(report_type, now_tz)
        counts, agent_stats = await _gather_data(db, period_start_utc)
        section_data = (
            await gather_report_sections(db, now_tz, template, report_type=report_type)
            if any(template.get(key) is True for key in (
                "includeAgentPerformance", "includeDailyAgentReport", "includeCategorySpecialists"
            )) else {}
        )

        # ── Subject ───────────────────────────────────────────────────────
        subject = report_subject(template, now_tz, report_type)

        # ── HTML body ─────────────────────────────────────────────────────
        # Import here to avoid circular import at module load time
        from app.routers.admin import _build_alert_html
        html_body = _build_alert_html(
            counts, now_tz,
            agent_stats=agent_stats,
            template=template,
            report_type=report_type,
            report_sections=section_data,
        )

        # ── Send ──────────────────────────────────────────────────────────
        from app.routers.admin import _dispatch_alert_email
        errors: list[str] = []
        for to_email in to_emails:
            try:
                await _dispatch_alert_email(send_params, to_email, subject, html_body)
            except Exception as exc:
                logger.warning(
                    "Report scheduler: send to %s failed — %s", to_email, exc
                )
                errors.append(str(exc))

        if errors and len(errors) == len(to_emails):
            raise RuntimeError(
                f"All {report_type} report deliveries failed: {errors[0]}"
            )

    async def _send_daily_monthly_report(
        self,
        db: AsyncSession,
        alert_cfg: AlertSettings,
        rep: dict,
        now_tz: datetime,
    ) -> None:
        recipients_cfg = alert_cfg.recipients or {}
        to_emails: list[str] = list(recipients_cfg.get("emails") or [])
        if recipients_cfg.get("includeAdmin", True):
            admin_res = await db.execute(
                select(User).where(User.role == "admin", User.is_active == True)
            )
            for admin_user in admin_res.scalars().all():
                addr = admin_user.username if "@" in (admin_user.username or "") else None
                if addr and addr not in to_emails:
                    to_emails.append(addr)
        if not to_emails:
            logger.warning("Daily + monthly report skipped — no recipients configured")
            return

        send_params = await _resolve_email_params(db, alert_cfg)
        subject, html_body = await build_daily_monthly_email(db, alert_cfg, now_tz)
        from app.routers.admin import _dispatch_alert_email
        errors: list[str] = []
        for to_email in to_emails:
            try:
                await _dispatch_alert_email(send_params, to_email, subject, html_body)
            except Exception as exc:
                logger.warning("Daily + monthly report send to %s failed — %s", to_email, exc)
                errors.append(str(exc))
        if errors and len(errors) == len(to_emails):
            raise RuntimeError(f"All daily + monthly report deliveries failed: {errors[0]}")


# ── Pure helpers (module-level, no class state) ────────────────────────────────

def _is_due(
    report_type: str,
    rep: dict,
    last_sent: dict,
    now_tz: datetime,
    today: date,
) -> bool:
    """Return True if this report type should be sent right now."""
    # Parse the configured HH:MM send time
    time_str = rep.get("time") or "08:00"
    try:
        h, m = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        logger.warning("Report scheduler: invalid time %r — skipping", time_str)
        return False

    # Current time must be at or past the scheduled time today
    scheduled_today = now_tz.replace(hour=h, minute=m, second=0, microsecond=0)
    if now_tz < scheduled_today:
        return False  # Not yet time today

    # Guard against duplicate sends on the same day
    last_str = last_sent.get(report_type)
    if last_str:
        try:
            last_dt = datetime.fromisoformat(last_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if last_dt.astimezone(now_tz.tzinfo).date() >= today:
                return False  # Already sent today
        except (ValueError, TypeError):
            pass  # Malformed timestamp — treat as never sent

    if report_type in ("daily", "dailyMonthly"):
        return True

    if report_type == "weekly":
        day_name = (rep.get("day") or "monday").lower()
        target   = _DAY_NAME_MAP.get(day_name, 0)
        return now_tz.weekday() == target

    if report_type == "monthly":
        try:
            dom = int(rep.get("dayOfMonth") or 1)
        except (ValueError, TypeError):
            dom = 1
        # Clamp to the actual last day of the month (e.g. 31 in February → 28/29)
        _, last_day = calendar.monthrange(today.year, today.month)
        return today.day == min(dom, last_day)

    return False


def _period_start(report_type: str, now_tz: datetime) -> datetime:
    """Return the UTC midnight that starts the current reporting period."""
    midnight = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    if report_type == "weekly":
        start_tz = midnight - timedelta(days=now_tz.weekday())   # Monday
    elif report_type == "monthly":
        start_tz = midnight.replace(day=1)
    else:
        start_tz = midnight  # daily
    
    return start_tz.astimezone(timezone.utc)


async def build_daily_monthly_email(
    db: AsyncSession,
    alert_cfg: AlertSettings,
    now_tz: datetime,
    reports_override: dict | None = None,
) -> tuple[str, str]:
    """Build the separate combined daily + month-to-date email and subject."""
    reports = reports_override or alert_cfg.reports or {}
    daily_template = dict((reports.get("daily") or {}).get("template") or {})
    monthly_template = dict(
        (reports.get("monthly") or {}).get("template") or daily_template
    )
    include_monthly = daily_template.get("includeDailyMonthlyReport", True) is not False
    # The combined email has one shared introduction; the monthly fragment
    # must not repeat it.
    monthly_template["intro"] = ""

    daily_start = _period_start("daily", now_tz)
    month_start = _period_start("monthly", now_tz)
    daily_counts, agent_stats = await _gather_data(db, daily_start)
    monthly_counts, _ = await _gather_data(db, month_start)

    daily_sections = (
        await gather_report_sections(db, now_tz, daily_template, report_type="daily")
        if any(daily_template.get(key) is True for key in (
            "includeAgentPerformance", "includeDailyAgentReport", "includeCategorySpecialists"
        )) else {}
    )
    monthly_sections = (
        await gather_report_sections(db, now_tz, monthly_template, report_type="monthly")
        if include_monthly and any(monthly_template.get(key) is True for key in (
            "includeAgentPerformance", "includeDailyAgentReport", "includeCategorySpecialists"
        )) else {}
    )

    from app.routers.admin import _build_alert_html

    def content_fragment(email_html: str) -> str:
        header_end = email_html.find("</td></tr>")
        footer_start = email_html.rfind(
            '<tr><td style="padding:20px 24px 28px;border-top:1px solid #f3f4f6;">'
        )
        if header_end < 0 or footer_start < 0 or footer_start <= header_end:
            raise RuntimeError("Could not extract report content for combined email")
        return email_html[header_end + len("</td></tr>"):footer_start]

    daily_html = _build_alert_html(
        daily_counts,
        now_tz,
        agent_stats=None,
        template=daily_template,
        report_type="daily",
        report_sections=daily_sections,
    )
    monthly_html = None
    if include_monthly:
        monthly_html = _build_alert_html(
            monthly_counts,
            now_tz,
            agent_stats=None,
            template=monthly_template,
            report_type="monthly",
            report_sections=monthly_sections,
        )

    date_text = now_tz.strftime("%B %d, %Y")
    report_label = "Daily + Monthly" if include_monthly else "Daily"
    subject = f"{report_label} Ticket Health Report — {date_text}"
    daily_block = (
        '<tr><td class="section-pad" style="padding:18px 26px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td bgcolor="#eef2ff" style="padding:11px 14px;background:#eef2ff;border-left:4px solid #4f46e5;'
        'font-family:Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:.7px;text-transform:uppercase;color:#3730a3;">'
        'Daily Report</td></tr>'
        + content_fragment(daily_html)
        + '</table></td></tr>'
    )
    monthly_block = ""
    if monthly_html:
        monthly_block = (
            '<tr><td class="section-pad" style="padding:24px 26px 0;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            '<tr><td bgcolor="#ecfdf5" style="padding:11px 14px;background:#ecfdf5;border-left:4px solid #059669;'
            'font-family:Arial,sans-serif;font-size:12px;font-weight:700;letter-spacing:.7px;text-transform:uppercase;color:#047857;">'
            'Monthly Report &nbsp;·&nbsp; Month to Date</td></tr>'
            + content_fragment(monthly_html)
            + '</table></td></tr>'
        )

    html_body = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>@media only screen and (max-width:640px){.report-shell{width:100%!important}.section-pad{padding-left:14px!important;padding-right:14px!important}}</style></head>'
        '<body style="margin:0;padding:0;background:#eef2f7;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#eef2f7" style="background:#eef2f7;">'
        '<tr><td align="center" style="padding:28px 12px;">'
        '<table role="presentation" class="report-shell" width="640" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" '
        'style="width:640px;max-width:640px;background:#ffffff;border:1px solid #e3e8f0;border-radius:14px;overflow:hidden;">'
        '<tr><td height="6" bgcolor="#818cf8" style="height:6px;background:#818cf8;font-size:0;line-height:0;">&nbsp;</td></tr>'
        '<tr><td bgcolor="#312e81" style="background:#312e81;padding:28px 30px 26px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td>'
        '<span style="display:inline-block;padding:6px 10px;border:1px solid #7772c8;border-radius:20px;'
        'font-family:Arial,sans-serif;font-size:10px;font-weight:700;color:#e0e7ff;letter-spacing:1px;text-transform:uppercase;">'
        f'{report_label} &nbsp;|&nbsp; 5:45 PM DIGEST</span>'
        '<div style="padding-top:15px;font-family:Arial,sans-serif;font-size:27px;line-height:1.2;font-weight:700;color:#ffffff;">Ticket Health Report</div>'
        f'<div style="padding-top:8px;font-family:Arial,sans-serif;font-size:13px;line-height:1.5;color:#c7d2fe;">{date_text} &nbsp;·&nbsp; {now_tz:%H:%M} {now_tz.tzname() or "local time"}</div>'
        '</td></tr></table></td></tr>'
        '<tr><td style="padding:20px 26px 2px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f5f6ff" '
        'style="background:#f5f6ff;border:1px solid #e5e7ff;border-radius:10px;"><tr>'
        '<td style="padding:12px 15px;font-family:Arial,sans-serif;font-size:12px;line-height:1.5;color:#4338ca;">'
        '<strong>Daily snapshot</strong> &nbsp;•&nbsp; '
        + ("<strong>Month to date</strong>" if include_monthly else "Monthly section hidden by your template selection")
        + '</td></tr></table></td></tr>'
        + daily_block
        + monthly_block
        + '<tr><td style="padding:23px 26px 25px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td height="1" bgcolor="#e8ecf2" style="height:1px;background:#e8ecf2;font-size:0;line-height:0;">&nbsp;</td>'
        '</tr><tr><td align="center" style="padding-top:16px;font-family:Arial,sans-serif;font-size:11px;line-height:1.7;color:#98a2b3;">'
        'Automated report from Tibos Helpdesk.<br>Manage report settings in <strong style="color:#667085;">Admin &rarr; Alerts</strong>.'
        '</td></tr></table></td></tr>'
        '</table></td></tr></table></body></html>'
    )
    return subject, html_body


async def _gather_data(
    db: AsyncSession, period_start: datetime
) -> tuple[dict, list]:
    """Gather ticket counts and per-agent stats from the DB."""
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
        select(func.count()).select_from(Ticket).where(
            Ticket.status == TicketStatus.on_hold
        )
    )
    open_now_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed])
        )
    )
    created_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.created_at >= period_start
        )
    )
    resolved_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.updated_at >= period_start,
            Ticket.status.in_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )

    counts = {
        "unassigned":     unassigned_res.scalar_one(),
        "sla_breach":     sla_res.scalar_one(),
        "on_hold":        on_hold_res.scalar_one(),
        "open_today":     open_now_res.scalar_one(),
        "created_today":  created_res.scalar_one(),
        "resolved_today": resolved_res.scalar_one(),
    }

    # Per-agent status breakdown (all active users, zeros if no tickets)
    all_users_res = await db.execute(select(User).where(User.is_active == True))
    all_users = all_users_res.scalars().all()

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

    _AVATAR_COLORS = [
        "#4f46e5", "#0891b2", "#059669", "#d97706",
        "#dc2626", "#7c3aed", "#0284c7", "#16a34a",
    ]
    agent_stats = sorted(
        [
            {
                "name": u.name or u.username,
                "initials": (
                    u.initials
                    or "".join(
                        w[0].upper()
                        for w in (u.name or u.username or "?").split()[:2]
                    )
                    or "?"
                ),
                "color": _AVATAR_COLORS[i % len(_AVATAR_COLORS)],
                "total":       int((ticket_rows[str(u.id)].total       if str(u.id) in ticket_rows else 0) or 0),
                "open":        int((ticket_rows[str(u.id)].open        if str(u.id) in ticket_rows else 0) or 0),
                "in_progress": int((ticket_rows[str(u.id)].in_progress if str(u.id) in ticket_rows else 0) or 0),
                "on_hold":     int((ticket_rows[str(u.id)].on_hold     if str(u.id) in ticket_rows else 0) or 0),
                "resolved": int(
                    ((ticket_rows[str(u.id)].resolved if str(u.id) in ticket_rows else 0) or 0)
                    + ((ticket_rows[str(u.id)].closed if str(u.id) in ticket_rows else 0) or 0)
                ),
            }
            for i, u in enumerate(all_users)
        ],
        key=lambda x: x["total"],
        reverse=True,
    )

    return counts, agent_stats


async def _resolve_email_params(db: AsyncSession, alert_cfg: AlertSettings) -> dict:
    """Resolve SMTP / M365 credentials — same logic as _run_test_alert."""
    alert_email_cfg = alert_cfg.alert_email_config or {}
    use_same = alert_email_cfg.get("useSameAsEmail", True)

    if use_same:
        sys_res = await db.execute(select(EmailConfig))
        sys_cfg: EmailConfig | None = sys_res.scalar_one_or_none()
        if not sys_cfg:
            raise RuntimeError(
                "Email not configured — set up SMTP/M365 in Admin → Email tab."
            )
        email_type = sys_cfg.type.value if sys_cfg.type else "smtp"

        if email_type == "smtp":
            from_addr = sys_cfg.smtp_from or sys_cfg.smtp_user or ""
            if not sys_cfg.smtp_host or not from_addr:
                raise RuntimeError(
                    "SMTP is incomplete — fill in Host and From Address in Admin → Email."
                )
            return {
                "method":    "smtp",
                "host":      sys_cfg.smtp_host,
                "port":      int(sys_cfg.smtp_port or 587),
                "security":  sys_cfg.smtp_security.value if sys_cfg.smtp_security else "tls",
                "user":      sys_cfg.smtp_user or "",
                "password":  sys_cfg.smtp_pass or "",
                "from_addr": from_addr,
            }
        if email_type == "m365":
            from_addr = sys_cfg.m365_from or ""
            if not sys_cfg.m365_tenant_id or not sys_cfg.m365_client_id or not from_addr:
                raise RuntimeError(
                    "M365 is incomplete — fill in Tenant ID, Client ID, and From Address."
                )
            return {
                "method":        "m365",
                "tenant_id":     sys_cfg.m365_tenant_id,
                "client_id":     sys_cfg.m365_client_id,
                "client_secret": sys_cfg.m365_client_secret or "",
                "from_addr":     from_addr,
            }
        raise RuntimeError(f"Unsupported email type: '{email_type}'")

    # Dedicated alert email config
    atype = alert_email_cfg.get("type", "smtp")
    if atype == "smtp":
        sc        = alert_email_cfg.get("smtp") or {}
        from_addr = sc.get("from") or sc.get("user") or ""
        if not sc.get("host") or not from_addr:
            raise RuntimeError(
                "Alert SMTP is incomplete — fill in Host and From Address in Alerts → Alert Email Account."
            )
        return {
            "method":    "smtp",
            "host":      sc.get("host", ""),
            "port":      int(sc.get("port") or 587),
            "security":  sc.get("security", "tls"),
            "user":      sc.get("user", ""),
            "password":  sc.get("pass", ""),
            "from_addr": from_addr,
        }
    if atype == "m365":
        mc        = alert_email_cfg.get("m365") or {}
        from_addr = mc.get("from", "")
        if not mc.get("tenantId") or not mc.get("clientId") or not from_addr:
            raise RuntimeError(
                "Alert M365 is incomplete — fill in Tenant ID, Client ID, and From Address."
            )
        return {
            "method":        "m365",
            "tenant_id":     mc.get("tenantId", ""),
            "client_id":     mc.get("clientId", ""),
            "client_secret": mc.get("clientSecret", ""),
            "from_addr":     from_addr,
        }
    raise RuntimeError(f"Unsupported alert email type: '{atype}'")


# ── Singleton ──────────────────────────────────────────────────────────────────
report_scheduler = ReportScheduler()
