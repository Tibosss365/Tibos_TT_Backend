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

All times are interpreted as UTC.

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
        today   = now_utc.date()

        async with AsyncSessionLocal() as db:
            result     = await db.execute(select(AlertSettings))
            alert_cfg: AlertSettings | None = result.scalar_one_or_none()
            if not alert_cfg:
                return

            reports   = alert_cfg.reports or {}
            last_sent = dict(alert_cfg.last_reports_sent or {})
            changed   = False

            for report_type in ("daily", "weekly", "monthly"):
                rep = reports.get(report_type) or {}
                if not rep.get("enabled", False):
                    continue
                if not _is_due(report_type, rep, last_sent, now_utc, today):
                    continue

                logger.info("Report scheduler: %s report is due — sending…", report_type)
                try:
                    await self._send_report(db, alert_cfg, report_type, rep, now_utc)
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
        now_utc: datetime,
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
        period_start = _period_start(report_type, now_utc)
        counts, agent_stats = await _gather_data(db, period_start)

        # ── Subject ───────────────────────────────────────────────────────
        default_subjects = {
            "daily":   "&#128202; Daily Helpdesk Report &#8212; {date}",
            "weekly":  "&#128198; Weekly Helpdesk Report &#8212; Week of {date}",
            "monthly": "&#128197;&#65039; Monthly Helpdesk Report &#8212; {month} {year}",
        }
        raw_subject = template.get("subject") or default_subjects[report_type]
        # Strip HTML entities from subject (email subjects are plain text)
        raw_subject = (
            raw_subject
            .replace("&#128202;", "📊").replace("&#128198;", "📆")
            .replace("&#128197;&#65039;", "🗓️").replace("&#8212;", "—")
        )
        day_str = f"{now_utc.day} {now_utc.strftime('%b')} {now_utc.year}"
        subject = (
            raw_subject
            .replace("{date}",        day_str)
            .replace("{month}",       now_utc.strftime("%B"))
            .replace("{year}",        str(now_utc.year))
            .replace("{system_name}", "Tibos Helpdesk")
        )

        # ── HTML body ─────────────────────────────────────────────────────
        # Import here to avoid circular import at module load time
        from app.routers.admin import _build_alert_html
        html_body = _build_alert_html(
            counts, now_utc,
            agent_stats=agent_stats,
            template=template,
            report_type=report_type,
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


# ── Pure helpers (module-level, no class state) ────────────────────────────────

def _is_due(
    report_type: str,
    rep: dict,
    last_sent: dict,
    now_utc: datetime,
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

    # Current time must be at or past the scheduled time today (UTC)
    scheduled_today = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
    if now_utc < scheduled_today:
        return False  # Not yet time today

    # Guard against duplicate sends on the same day
    last_str = last_sent.get(report_type)
    if last_str:
        try:
            last_dt = datetime.fromisoformat(last_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if last_dt.date() >= today:
                return False  # Already sent today
        except (ValueError, TypeError):
            pass  # Malformed timestamp — treat as never sent

    if report_type == "daily":
        return True

    if report_type == "weekly":
        day_name = (rep.get("day") or "monday").lower()
        target   = _DAY_NAME_MAP.get(day_name, 0)
        return now_utc.weekday() == target

    if report_type == "monthly":
        try:
            dom = int(rep.get("dayOfMonth") or 1)
        except (ValueError, TypeError):
            dom = 1
        # Clamp to the actual last day of the month (e.g. 31 in February → 28/29)
        _, last_day = calendar.monthrange(today.year, today.month)
        return today.day == min(dom, last_day)

    return False


def _period_start(report_type: str, now_utc: datetime) -> datetime:
    """Return the UTC midnight that starts the current reporting period."""
    midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if report_type == "weekly":
        return midnight - timedelta(days=now_utc.weekday())   # Monday
    if report_type == "monthly":
        return midnight.replace(day=1)
    return midnight  # daily


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
