"""
Enterprise SLA Service
======================

Rules (respects SLAConfig settings)
-------------------------------------
- timer_start = "on_creation":      SLA clock starts when ticket is created.
- timer_start = "on_assignment":     SLA clock starts only when first assigned to an agent.
- countdown_mode = "24_7":           SLA counts every wall-clock hour.
- countdown_mode = "business_hours": SLA only counts time inside configured work days/hours.
- pause_on:   SLA pauses when ticket enters any of these statuses (default: on-hold).
- stop:       SLA completes when ticket is resolved or closed.

Public API
----------
  SLAService.start(ticket, db, is_assignment=False)  → start timer
  SLAService.pause(ticket, db)          → pause timer
  SLAService.resume(ticket, db)         → resume, extending deadline correctly
  SLAService.stop(ticket, db)           → mark completed (resolved/closed)
  SLAService.recalculate(ticket, db)    → recalculate due time after priority change
  SLAService.handle_status_change(...)  → apply correct SLA action for status transition
  SLABreachDetector.start()             → start background asyncio task
  SLABreachDetector.stop()              → cancel it
  SLABreachDetector.check_breaches()    → run one check cycle
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.admin import SLAConfig
from app.models.ticket import SLAStatus, Ticket, TicketPriority, TicketStatus

logger = logging.getLogger(__name__)

# ── Default SLA hours by priority ─────────────────────────────────────────────
DEFAULT_HOURS: dict[TicketPriority, int] = {
    TicketPriority.critical: 1,
    TicketPriority.high:     4,
    TicketPriority.medium:   8,
    TicketPriority.low:      24,
}

DEFAULT_PAUSE_STATUSES = {"on-hold"}
STOP_STATUSES = {TicketStatus.resolved, TicketStatus.closed}


# ── Config helpers ─────────────────────────────────────────────────────────────

def _hours_for(priority: TicketPriority, cfg: SLAConfig | None) -> int:
    if cfg:
        mapping = {
            TicketPriority.critical: cfg.critical_hours,
            TicketPriority.high:     cfg.high_hours,
            TicketPriority.medium:   cfg.medium_hours,
            TicketPriority.low:      cfg.low_hours,
        }
        return mapping.get(priority, DEFAULT_HOURS[priority])
    return DEFAULT_HOURS.get(priority, 8)


def _pause_statuses(cfg: SLAConfig | None) -> set[str]:
    if cfg and cfg.pause_on:
        return set(cfg.pause_on)
    return DEFAULT_PAUSE_STATUSES


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_duration(total_seconds: int) -> str:
    """Format seconds into human-readable 'Xh YYm'."""
    total_seconds = abs(total_seconds)
    days  = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    mins  = (total_seconds % 3600) // 60
    secs  = total_seconds % 60
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {mins:02d}m"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


# ── Business Hours helpers ─────────────────────────────────────────────────────

def _parse_work_bounds(cfg: SLAConfig):
    """Return (work_days_set, open_td, close_td, ws_h, ws_m, we_h, we_m)."""
    ws_h, ws_m = map(int, cfg.work_start.split(":"))
    we_h, we_m = map(int, cfg.work_end.split(":"))
    work_days_set = set(cfg.work_days)  # 0=Mon … 6=Sun
    open_td  = timedelta(hours=ws_h, minutes=ws_m)
    close_td = timedelta(hours=we_h, minutes=we_m)
    return work_days_set, open_td, close_td, ws_h, ws_m, we_h, we_m


def _time_of_day_td(dt: datetime) -> timedelta:
    return timedelta(hours=dt.hour, minutes=dt.minute, seconds=dt.second)


def _advance_to_business(
    dt: datetime,
    work_days_set: set,
    open_td: timedelta,
    close_td: timedelta,
    ws_h: int,
    ws_m: int,
) -> datetime:
    """
    Advance dt to the next moment inside business hours.
    Returns dt unchanged if already inside business hours.
    Scans at most 14 calendar days (handles weekends + holidays).
    """
    for _ in range(14):
        tod = _time_of_day_td(dt)
        if dt.weekday() in work_days_set and open_td <= tod < close_td:
            return dt  # already inside business hours
        if dt.weekday() not in work_days_set or tod >= close_td:
            # Non-work day or past close → next calendar day at open
            dt = (dt + timedelta(days=1)).replace(
                hour=ws_h, minute=ws_m, second=0, microsecond=0
            )
        else:
            # Before open today → move to today's open
            dt = dt.replace(hour=ws_h, minute=ws_m, second=0, microsecond=0)
    return dt  # unreachable with sane config


def _add_business_hours(start: datetime, hours: float, cfg: SLAConfig) -> datetime:
    """
    Advance `start` by exactly `hours` worth of business-hours time.
    Non-work days and time outside work_start–work_end are skipped.
    Falls back to wall-clock if the business window is zero/invalid.
    """
    work_days_set, open_td, close_td, ws_h, ws_m, we_h, we_m = _parse_work_bounds(cfg)

    # Guard: zero or negative work window → fall back to simple wall clock
    work_secs_per_day = int((close_td - open_td).total_seconds())
    if work_secs_per_day <= 0 or not work_days_set:
        return _ensure_utc(start) + timedelta(hours=hours)

    remaining = int(hours * 3600)
    current   = _ensure_utc(start)
    current   = _advance_to_business(current, work_days_set, open_td, close_td, ws_h, ws_m)

    while remaining > 0:
        day_close_dt = current.replace(
            hour=we_h, minute=we_m, second=0, microsecond=0
        )
        avail = max(0, int((day_close_dt - current).total_seconds()))

        if avail == 0:
            # At or past close; move to next business window
            next_open = (current + timedelta(days=1)).replace(
                hour=ws_h, minute=ws_m, second=0, microsecond=0
            )
            current = _advance_to_business(next_open, work_days_set, open_td, close_td, ws_h, ws_m)
            continue

        if remaining <= avail:
            current += timedelta(seconds=remaining)
            remaining = 0
        else:
            remaining -= avail
            next_open = (current + timedelta(days=1)).replace(
                hour=ws_h, minute=ws_m, second=0, microsecond=0
            )
            current = _advance_to_business(next_open, work_days_set, open_td, close_td, ws_h, ws_m)

    return current


def _business_hours_elapsed(start: datetime, end: datetime, cfg: SLAConfig) -> float:
    """
    Return the number of business hours between start and end.
    Used to preserve remaining SLA time across pause/resume cycles.
    """
    if end <= start:
        return 0.0

    work_days_set, open_td, close_td, ws_h, ws_m, we_h, we_m = _parse_work_bounds(cfg)
    work_secs_per_day = int((close_td - open_td).total_seconds())
    if work_secs_per_day <= 0 or not work_days_set:
        return (end - start).total_seconds() / 3600.0

    total_secs = 0
    current    = _ensure_utc(start)
    end_utc    = _ensure_utc(end)

    while current < end_utc:
        tod = _time_of_day_td(current)
        if current.weekday() not in work_days_set or tod >= close_td:
            next_open = (current + timedelta(days=1)).replace(
                hour=ws_h, minute=ws_m, second=0, microsecond=0
            )
            current = _advance_to_business(next_open, work_days_set, open_td, close_td, ws_h, ws_m)
            continue
        if tod < open_td:
            current = current.replace(hour=ws_h, minute=ws_m, second=0, microsecond=0)
            continue

        # Inside business hours — count until close or end, whichever comes first
        day_close_dt = current.replace(hour=we_h, minute=we_m, second=0, microsecond=0)
        window_end   = min(day_close_dt, end_utc)
        total_secs  += int((window_end - current).total_seconds())
        current      = day_close_dt  # jump to end of window

    return total_secs / 3600.0


def _calculate_due_time(t0: datetime, hours: int, cfg: SLAConfig | None) -> datetime:
    """Calculate the SLA due time from t0, respecting countdown_mode."""
    if cfg and cfg.countdown_mode == "business_hours":
        return _add_business_hours(t0, hours, cfg)
    return _ensure_utc(t0) + timedelta(hours=hours)


# ── SLAService ────────────────────────────────────────────────────────────────

class SLAService:
    """Stateless SLA operations. All methods operate on a Ticket ORM object."""

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @staticmethod
    async def start(
        ticket: Ticket,
        db: AsyncSession,
        start_time: datetime | None = None,
        is_assignment: bool = False,
    ) -> None:
        """
        Start the SLA timer, respecting the timer_start config:
          - "on_creation":   starts on ticket creation (is_assignment not required)
          - "on_assignment": starts only when an agent is first assigned

        Args:
            start_time:    Override the clock-start moment. Defaults to now.
            is_assignment: Set True when called from an agent-assignment event.
        """
        current = ticket.sla_status
        if current is not None and current != SLAStatus.not_started:
            logger.debug(f"SLA already started for {ticket.ticket_id} ({current})")
            return

        cfg          = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        timer_start  = cfg.timer_start if cfg else "on_creation"

        # Honour the timer_start policy
        if timer_start == "on_assignment" and not is_assignment:
            logger.debug(
                f"SLA deferred for {ticket.ticket_id} "
                f"(timer_start=on_assignment — waiting for agent assignment)"
            )
            return  # leave sla_status = not_started until assignment

        hours = _hours_for(ticket.priority, cfg)
        t0    = _ensure_utc(start_time) if start_time else datetime.now(timezone.utc)
        due   = _calculate_due_time(t0, hours, cfg)

        ticket.sla_start_time     = t0
        ticket.sla_due_time       = due
        ticket.sla_due_at         = due  # keep legacy field in sync
        ticket.sla_status         = SLAStatus.active
        ticket.sla_paused_at      = None
        ticket.sla_paused_seconds = 0

        if due < datetime.now(timezone.utc):
            ticket.sla_status = SLAStatus.overdue

        logger.info(
            f"SLA started for {ticket.ticket_id} | priority={ticket.priority.value} "
            f"| hours={hours} | mode={cfg.countdown_mode if cfg else '24_7'} "
            f"| timer_start={timer_start} "
            f"| start={t0.isoformat()} | due={due.isoformat()}"
        )

    @staticmethod
    async def pause(ticket: Ticket, db: AsyncSession) -> None:
        """Pause the SLA timer. No-op if not currently active."""
        if ticket.sla_status != SLAStatus.active:
            return
        ticket.sla_status    = SLAStatus.paused
        ticket.sla_paused_at = datetime.now(timezone.utc)
        logger.info(f"SLA paused for {ticket.ticket_id}")

    @staticmethod
    async def resume(ticket: Ticket, db: AsyncSession) -> None:
        """
        Resume a paused SLA timer, extending the deadline to preserve the
        exact remaining time (business hours or wall-clock, matching the config).
        """
        if ticket.sla_status != SLAStatus.paused:
            return

        now = datetime.now(timezone.utc)
        cfg = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        countdown = cfg.countdown_mode if cfg else "24_7"

        if ticket.sla_paused_at and ticket.sla_due_time:
            paused_since = _ensure_utc(ticket.sla_paused_at)
            due          = _ensure_utc(ticket.sla_due_time)
            pause_secs   = max(0, int((now - paused_since).total_seconds()))

            if countdown == "business_hours" and cfg:
                # Preserve the remaining business hours at the time of pause
                remaining_biz = max(0.0, _business_hours_elapsed(paused_since, due, cfg))
                new_due = _add_business_hours(now, remaining_biz, cfg)
            else:
                # 24/7: extend deadline by the wall-clock pause duration
                new_due = due + timedelta(seconds=pause_secs)

            ticket.sla_due_time       = new_due
            ticket.sla_due_at         = new_due  # legacy
            ticket.sla_paused_seconds = (ticket.sla_paused_seconds or 0) + pause_secs

        ticket.sla_paused_at = None

        if ticket.sla_due_time and now > _ensure_utc(ticket.sla_due_time):
            ticket.sla_status = SLAStatus.overdue
        else:
            ticket.sla_status = SLAStatus.active

        logger.info(
            f"SLA resumed for {ticket.ticket_id} | "
            f"new_due={ticket.sla_due_time} | status={ticket.sla_status.value}"
        )

    @staticmethod
    async def stop(ticket: Ticket, db: AsyncSession) -> None:
        """Stop the SLA — ticket resolved or closed."""
        if ticket.sla_status in (SLAStatus.completed, SLAStatus.not_started):
            return
        ticket.sla_status    = SLAStatus.completed
        ticket.sla_paused_at = None
        logger.info(f"SLA completed for {ticket.ticket_id}")

    @staticmethod
    async def recalculate(
        ticket: Ticket,
        db: AsyncSession,
        new_priority: TicketPriority | None = None,
    ) -> None:
        """
        Recalculate sla_due_time after a priority change.
        Preserves sla_start_time; only the deadline shifts.
        Respects countdown_mode.
        """
        if ticket.sla_status == SLAStatus.not_started or not ticket.sla_start_time:
            return

        cfg      = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        priority = new_priority or ticket.priority
        hours    = _hours_for(priority, cfg)
        start    = _ensure_utc(ticket.sla_start_time)
        new_due  = _calculate_due_time(start, hours, cfg)

        # For 24/7 mode, add back accumulated pause seconds so they aren't lost
        # Business-hours mode handles this inside pause/resume via _business_hours_elapsed
        countdown = cfg.countdown_mode if cfg else "24_7"
        if ticket.sla_paused_seconds and countdown != "business_hours":
            new_due += timedelta(seconds=ticket.sla_paused_seconds)

        ticket.sla_due_time = new_due
        ticket.sla_due_at   = new_due

        now = datetime.now(timezone.utc)
        if ticket.sla_status == SLAStatus.active and now > new_due:
            ticket.sla_status = SLAStatus.overdue
        elif ticket.sla_status == SLAStatus.overdue and now <= new_due:
            ticket.sla_status = SLAStatus.active

        logger.info(
            f"SLA recalculated for {ticket.ticket_id} | priority={priority.value} "
            f"| hours={hours} | new_due={new_due.isoformat()}"
        )

    # ── Calculation helpers ────────────────────────────────────────────────

    @staticmethod
    def get_remaining_seconds(ticket: Ticket) -> int:
        """
        Seconds remaining until SLA due time.
        Positive → time left. Negative → overdue. 0 → not started / completed.
        """
        status = ticket.sla_status
        if not ticket.sla_due_time or status in (
            None, SLAStatus.not_started, SLAStatus.completed
        ):
            return 0

        due = _ensure_utc(ticket.sla_due_time)

        # When paused, measure remaining from the pause moment (frozen)
        if status == SLAStatus.paused and ticket.sla_paused_at:
            paused_at = _ensure_utc(ticket.sla_paused_at)
            return int((due - paused_at).total_seconds())

        return int((due - datetime.now(timezone.utc)).total_seconds())

    @staticmethod
    def get_overdue_seconds(ticket: Ticket) -> int:
        return max(0, -SLAService.get_remaining_seconds(ticket))

    @staticmethod
    def get_status_info(ticket: Ticket) -> dict:
        status    = ticket.sla_status or SLAStatus.not_started
        remaining = SLAService.get_remaining_seconds(ticket)
        overdue   = SLAService.get_overdue_seconds(ticket)
        is_overdue = (
            status == SLAStatus.overdue
            or (status == SLAStatus.active and remaining < 0)
        )
        return {
            "sla_status":              status.value if status else "not_started",
            "sla_start_time":          ticket.sla_start_time.isoformat() if ticket.sla_start_time else None,
            "sla_due_time":            ticket.sla_due_time.isoformat()   if ticket.sla_due_time   else None,
            "sla_remaining_seconds":   max(remaining, 0),
            "sla_overdue_seconds":     overdue,
            "sla_paused_seconds":      ticket.sla_paused_seconds or 0,
            "sla_remaining_display":   _fmt_duration(remaining) if remaining > 0 else None,
            "sla_overdue_display":     f"{_fmt_duration(overdue)} overdue" if overdue > 0 else None,
            "is_overdue":              is_overdue,
            "is_paused":               status == SLAStatus.paused,
            "is_completed":            status == SLAStatus.completed,
        }

    # ── Status-change dispatcher ───────────────────────────────────────────

    @staticmethod
    async def handle_status_change(
        ticket: Ticket,
        new_status: TicketStatus,
        old_status: TicketStatus,
        db: AsyncSession,
    ) -> None:
        """
        Apply the correct SLA action for a ticket status transition.
        Called from the PATCH endpoint after the new status is validated.
        """
        cfg      = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        pause_on = _pause_statuses(cfg)

        new_str = new_status.value if hasattr(new_status, "value") else str(new_status)
        old_str = old_status.value if hasattr(old_status, "value") else str(old_status)

        entering_pause = new_str in pause_on and old_str not in pause_on
        leaving_pause  = old_str in pause_on and new_str not in pause_on
        entering_stop  = new_status in STOP_STATUSES

        if entering_stop:
            await SLAService.stop(ticket, db)
        elif entering_pause:
            await SLAService.pause(ticket, db)
        elif leaving_pause:
            await SLAService.resume(ticket, db)


# ── SLA Breach Detector (background job) ─────────────────────────────────────

class SLABreachDetector:
    """
    Background asyncio task (runs every 60 s).
    Finds tickets with sla_status=active whose sla_due_time has passed
    and marks them overdue.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop(), name="sla-breach-detector")
            logger.info("SLA breach detector started (interval: 60 s)")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("SLA breach detector stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(60)
                count = await self.check_breaches()
                if count:
                    logger.info(f"SLA breach detector: {count} ticket(s) marked overdue")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SLA breach detector error: {e}")

    async def check_breaches(self) -> int:
        """Mark all active tickets whose sla_due_time has elapsed as overdue."""
        now     = datetime.now(timezone.utc)
        updated = 0
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Ticket).where(
                    Ticket.sla_status.cast(String) == SLAStatus.active.value,
                    Ticket.sla_due_time.isnot(None),
                    Ticket.sla_due_time < now,
                )
            )
            tickets = result.scalars().all()
            for ticket in tickets:
                ticket.sla_status = SLAStatus.overdue
                updated += 1
            if tickets:
                await db.commit()
                logger.info(
                    f"SLA breach check: {updated} ticket(s) newly overdue at {now.isoformat()}"
                )
        return updated


# ── Singleton ─────────────────────────────────────────────────────────────────
sla_breach_detector = SLABreachDetector()
