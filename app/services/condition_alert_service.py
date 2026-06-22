"""
Condition Alert Service
=======================
Background asyncio service that turns the Admin → Alerts "Alert Conditions"
toggles into real emails.  Runs every 60 s and evaluates the enabled
conditions against live tickets:

- ``unassigned``  : open / in-progress tickets with no assignee for longer
                    than ``thresholdMins``
- ``slaBreach``   : tickets past their SLA due time — plus an optional
                    80 %-elapsed early warning when ``includeWarning`` is on
- ``onHold``      : tickets sitting in on-hold for longer than ``thresholdHours``
- ``inProgress``  : tickets stuck in in-progress for longer than ``thresholdHours``

(``openToday`` is a report-only metric — it is covered by the scheduled
daily / weekly / monthly digests, not by real-time alerts.)

All tickets that newly match are batched into ONE digest email per tick and
sent to the configured recipients, using the same credential resolution as
the report scheduler (dedicated alert account or the system email config).

Dedup
-----
``alert_settings.last_condition_alerts`` stores, per condition, the tickets
already alerted: ``{"unassigned": {"<uuid>": "<iso-ts>"}, ...}``.  A ticket
is alerted at most once per condition while it remains in that state;
entries are pruned when the ticket leaves the state, so re-entering the
state alerts again.  Tickets are only marked alerted after the email is
actually delivered, so a transient SMTP failure retries on the next tick.
"""

import asyncio
import logging
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, and_, func, or_, select

from app.database import AsyncSessionLocal
from app.models.admin import AlertSettings
from app.models.ticket import (
    SLAStatus,
    Ticket,
    TicketStatus,
    TicketTimeline,
    TimelineType,
)
from app.models.user import User
from app.services.report_scheduler import _resolve_email_params

logger = logging.getLogger(__name__)

# Condition key → (emoji, human label) for the digest email sections
_CONDITION_META = {
    "unassigned": ("🙋", "Unassigned Tickets"),
    "slaWarning": ("⏳", "SLA Warning (80% elapsed)"),
    "slaBreach":  ("🚨", "SLA Breach"),
    "onHold":     ("⏸️", "On-Hold Too Long"),
    "inProgress": ("⏱️", "Long-Running In Progress"),
}

_ACTIVE_STATUSES = [TicketStatus.open, TicketStatus.in_progress]


class ConditionAlertService:
    """
    Background asyncio task — same start/stop pattern as ReportScheduler.
    Registered in main.py lifespan.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(
                self._loop(), name="condition-alert-service"
            )
            logger.info("Condition alert service started (checks every 60 s)")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Condition alert service stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    # ── Main loop ──────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        # Brief pause on startup so migrations / seeding finish first
        await asyncio.sleep(45)
        while self._running:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Condition alert service error: %s", exc, exc_info=True)
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    # ── One evaluation tick ────────────────────────────────────────────────

    async def _check_once(self) -> None:
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AlertSettings).limit(1))
            alert_cfg: AlertSettings | None = result.scalar_one_or_none()
            if not alert_cfg:
                return

            conditions = alert_cfg.conditions or {}

            # current[cond_key] = {ticket_uuid_str: (ticket, detail_text)}
            current: dict[str, dict[str, tuple[Ticket, str]]] = {}

            # ── Gather tickets currently matching each enabled condition ──
            cond = conditions.get("unassigned") or {}
            if cond.get("enabled"):
                current["unassigned"] = await self._find_unassigned(db, cond, now)

            cond = conditions.get("slaBreach") or {}
            if cond.get("enabled"):
                current["slaBreach"] = await self._find_sla_breached(db, now)
                if cond.get("includeWarning"):
                    current["slaWarning"] = await self._find_sla_warning(db, now)

            cond = conditions.get("onHold") or {}
            if cond.get("enabled"):
                current["onHold"] = await self._find_stuck_in_status(
                    db, TicketStatus.on_hold, cond, now, "On hold"
                )

            cond = conditions.get("inProgress") or {}
            if cond.get("enabled"):
                current["inProgress"] = await self._find_stuck_in_status(
                    db, TicketStatus.in_progress, cond, now, "In progress"
                )

            # ── Diff against dedup state ──────────────────────────────────
            state: dict = dict(alert_cfg.last_condition_alerts or {})

            new_hits: dict[str, list[tuple[Ticket, str]]] = {}
            for key, matches in current.items():
                already: dict = state.get(key) or {}
                fresh = [v for tid, v in matches.items() if tid not in already]
                if fresh:
                    new_hits[key] = fresh

            # ── Send the digest for newly matching tickets ────────────────
            sent_ok = False
            if new_hits:
                try:
                    await self._send_digest(db, alert_cfg, new_hits, now)
                    sent_ok = True
                except Exception as exc:
                    # Leave state untouched for the new tickets → retried next tick
                    logger.error("Condition alert send failed: %s", exc)

            # ── Rebuild state: keep only tickets still matching ───────────
            new_state: dict = {}
            for key, matches in current.items():
                already: dict = state.get(key) or {}
                entry: dict[str, str] = {}
                for tid in matches:
                    if tid in already:
                        entry[tid] = already[tid]
                    elif sent_ok and key in new_hits:
                        entry[tid] = now.isoformat()
                if entry:
                    new_state[key] = entry

            if new_state != (alert_cfg.last_condition_alerts or {}):
                alert_cfg.last_condition_alerts = new_state
                await db.commit()

            if new_hits and sent_ok:
                total = sum(len(v) for v in new_hits.values())
                logger.info(
                    "Condition alert sent for %d ticket(s): %s",
                    total, {k: len(v) for k, v in new_hits.items()},
                )

    # ── Condition queries ──────────────────────────────────────────────────

    async def _find_unassigned(
        self, db, cond: dict, now: datetime
    ) -> dict[str, tuple[Ticket, str]]:
        try:
            mins = int(cond.get("thresholdMins") or 30)
        except (ValueError, TypeError):
            mins = 30
        cutoff = now - timedelta(minutes=mins)
        res = await db.execute(
            select(Ticket).where(
                Ticket.is_deleted == False,  # noqa: E712
                Ticket.assignee_id.is_(None),
                Ticket.status.in_(_ACTIVE_STATUSES),
                Ticket.created_at <= cutoff,
            )
        )
        return {
            str(t.id): (t, f"Unassigned for {_fmt_age(now - _utc(t.created_at))}")
            for t in res.scalars().all()
        }

    async def _find_sla_breached(
        self, db, now: datetime
    ) -> dict[str, tuple[Ticket, str]]:
        res = await db.execute(
            select(Ticket).where(
                Ticket.is_deleted == False,  # noqa: E712
                Ticket.status.in_(_ACTIVE_STATUSES),
                or_(
                    and_(
                        Ticket.sla_due_time.isnot(None),
                        Ticket.sla_due_time < now,
                    ),
                    Ticket.sla_status.cast(String) == SLAStatus.overdue.value,
                ),
            )
        )
        out: dict[str, tuple[Ticket, str]] = {}
        for t in res.scalars().all():
            if t.sla_due_time:
                detail = f"SLA overdue by {_fmt_age(now - _utc(t.sla_due_time))}"
            else:
                detail = "SLA overdue"
            out[str(t.id)] = (t, detail)
        return out

    async def _find_sla_warning(
        self, db, now: datetime
    ) -> dict[str, tuple[Ticket, str]]:
        """Tickets with an active SLA that is >= 80 % elapsed but not yet due."""
        res = await db.execute(
            select(Ticket).where(
                Ticket.is_deleted == False,  # noqa: E712
                Ticket.status.in_(_ACTIVE_STATUSES),
                Ticket.sla_status.cast(String) == SLAStatus.active.value,
                Ticket.sla_start_time.isnot(None),
                Ticket.sla_due_time.isnot(None),
                Ticket.sla_due_time > now,
            )
        )
        out: dict[str, tuple[Ticket, str]] = {}
        for t in res.scalars().all():
            start, due = _utc(t.sla_start_time), _utc(t.sla_due_time)
            total = (due - start).total_seconds()
            if total <= 0:
                continue
            elapsed = (now - start).total_seconds()
            pct = elapsed / total
            if pct >= 0.8:
                out[str(t.id)] = (
                    t,
                    f"SLA {int(pct * 100)}% elapsed — due in {_fmt_age(due - now)}",
                )
        return out

    async def _find_stuck_in_status(
        self, db, status: TicketStatus, cond: dict, now: datetime, label: str
    ) -> dict[str, tuple[Ticket, str]]:
        try:
            hours = int(cond.get("thresholdHours") or 24)
        except (ValueError, TypeError):
            hours = 24
        cutoff = now - timedelta(hours=hours)

        res = await db.execute(
            select(Ticket).where(
                Ticket.is_deleted == False,  # noqa: E712
                Ticket.status == status,
            )
        )
        tickets = res.scalars().all()
        if not tickets:
            return {}

        # When did each ticket enter its current status?  Latest status-change
        # timeline entry; tickets that never changed status use created_at.
        tl_res = await db.execute(
            select(
                TicketTimeline.ticket_id,
                func.max(TicketTimeline.created_at).label("entered"),
            )
            .where(
                TicketTimeline.ticket_id.in_([t.id for t in tickets]),
                TicketTimeline.type == TimelineType.status,
            )
            .group_by(TicketTimeline.ticket_id)
        )
        entered_map = {str(r.ticket_id): _utc(r.entered) for r in tl_res.fetchall()}

        out: dict[str, tuple[Ticket, str]] = {}
        for t in tickets:
            entered = entered_map.get(str(t.id)) or _utc(t.created_at)
            if entered <= cutoff:
                out[str(t.id)] = (t, f"{label} for {_fmt_age(now - entered)}")
        return out

    # ── Digest builder + sender ────────────────────────────────────────────

    async def _send_digest(
        self,
        db,
        alert_cfg: AlertSettings,
        new_hits: dict[str, list[tuple[Ticket, str]]],
        now: datetime,
    ) -> None:
        # Recipients — same resolution as the report scheduler
        recipients_cfg = alert_cfg.recipients or {}
        to_emails: list[str] = list(recipients_cfg.get("emails") or [])

        if recipients_cfg.get("includeAdmin", True):
            admin_res = await db.execute(
                select(User).where(User.role == "admin", User.is_active == True)  # noqa: E712
            )
            for admin_user in admin_res.scalars().all():
                addr = admin_user.username if "@" in (admin_user.username or "") else None
                if addr and addr not in to_emails:
                    to_emails.append(addr)

        if not to_emails:
            raise RuntimeError("No alert recipients configured")

        send_params = await _resolve_email_params(db, alert_cfg)

        total = sum(len(v) for v in new_hits.values())
        subject = f"🚨 Helpdesk Alert — {total} ticket{'s' if total != 1 else ''} need attention"
        html_body = _build_digest_html(new_hits, now)

        # Lazy import — admin router imports models that import this package
        from app.routers.admin import _dispatch_alert_email

        errors: list[str] = []
        for to_email in to_emails:
            try:
                await _dispatch_alert_email(send_params, to_email, subject, html_body)
            except Exception as exc:
                logger.warning("Condition alert send to %s failed — %s", to_email, exc)
                errors.append(str(exc))

        if errors and len(errors) == len(to_emails):
            raise RuntimeError(f"All alert deliveries failed: {errors[0]}")


# ── Pure helpers ────────────────────────────────────────────────────────────

def _utc(dt: datetime) -> datetime:
    """Naive DB datetimes are stored as UTC — tag them so arithmetic works."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_age(delta: timedelta) -> str:
    """'3d 4h', '2h 15m', '45m'."""
    total_min = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(total_min, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def _build_digest_html(
    new_hits: dict[str, list[tuple[Ticket, str]]], now: datetime
) -> str:
    sections = []
    for key in ("slaBreach", "slaWarning", "unassigned", "onHold", "inProgress"):
        hits = new_hits.get(key)
        if not hits:
            continue
        emoji, label = _CONDITION_META[key]
        rows = "".join(
            f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-family:monospace;white-space:nowrap;">{_esc(t.ticket_id)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{_esc(t.subject)}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-transform:capitalize;white-space:nowrap;">{_esc(t.priority.value if t.priority else "")}</td>
              <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">{_esc(detail)}</td>
            </tr>"""
            for t, detail in hits
        )
        sections.append(
            f"""
        <h3 style="margin:24px 0 8px;font-size:15px;color:#111827;">{emoji} {label} ({len(hits)})</h3>
        <table style="border-collapse:collapse;width:100%;font-size:13px;color:#374151;">
          <tr style="background:#f9fafb;text-align:left;">
            <th style="padding:8px 12px;border-bottom:2px solid #e5e7eb;">Ticket</th>
            <th style="padding:8px 12px;border-bottom:2px solid #e5e7eb;">Subject</th>
            <th style="padding:8px 12px;border-bottom:2px solid #e5e7eb;">Priority</th>
            <th style="padding:8px 12px;border-bottom:2px solid #e5e7eb;">Detail</th>
          </tr>{rows}
        </table>"""
        )

    return f"""
<div style="max-width:680px;margin:0 auto;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
  <div style="background:#4f46e5;color:#ffffff;padding:18px 24px;border-radius:8px 8px 0 0;">
    <h2 style="margin:0;font-size:18px;">Tibos Helpdesk — Ticket Alerts</h2>
    <p style="margin:4px 0 0;font-size:12px;opacity:.85;">{now.strftime("%d %b %Y, %H:%M")} UTC</p>
  </div>
  <div style="background:#ffffff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:8px 24px 24px;">
    {"".join(sections)}
    <p style="margin-top:24px;font-size:11px;color:#9ca3af;">
      You receive this because alert conditions are enabled in
      Admin → Alerts. Each ticket is alerted once per condition.
    </p>
  </div>
</div>"""


# ── Singleton ───────────────────────────────────────────────────────────────
condition_alert_service = ConditionAlertService()
