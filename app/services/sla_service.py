"""
Enterprise SLA Service
======================

Rules
-----
- SLA starts ONLY when a ticket is BOTH created AND assigned to an agent.
- Priority deadlines (configurable in SLAConfig, defaults below):
    Critical → 1 h   High → 4 h   Medium → 8 h   Low → 24 h
- Pause: when ticket moves into a "pause" status (default: on-hold).
  Accumulated pause time is stored so the deadline extends correctly on resume.
- Stop: when ticket is resolved or closed → sla_status = completed.
- Breach detection: a background job runs every 60 s and marks active tickets
  whose sla_due_time has passed as overdue.

Public API
----------
  SLAService.start(ticket, db)          → start timer (called on first assignment)
  SLAService.pause(ticket, db)          → pause timer
  SLAService.resume(ticket, db)         → resume timer, extend deadline
  SLAService.stop(ticket, db)           → mark completed (resolved/closed)
  SLAService.get_remaining_seconds(t)   → int (negative = overdue)
  SLAService.get_status_info(t)         → dict for API responses
  SLAService.recalculate(ticket, db)    → recalculate due time after priority change

  SLABreachDetector.start()             → start background asyncio task
  SLABreachDetector.stop()              → cancel it
  SLABreachDetector.check_breaches()    → run one check cycle (also callable on demand)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
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

# Statuses that pause the SLA timer (can be overridden by SLAConfig.pause_on)
DEFAULT_PAUSE_STATUSES = {"on-hold"}

# Statuses that stop the SLA timer
STOP_STATUSES = {TicketStatus.resolved, TicketStatus.closed}


# ── Internal helpers ───────────────────────────────────────────────────────────

def _hours_for(priority: TicketPriority, cfg: SLAConfig | None) -> int:
    """Return SLA hours for the given priority, using config if available."""
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
    """Format seconds into human-readable 'Xh YYm' or 'YYm ZZs'."""
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


# ── SLAService ────────────────────────────────────────────────────────────────

class SLAService:
    """Stateless SLA operations. All methods take a Ticket ORM object."""

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @staticmethod
    async def start(ticket: Ticket, db: AsyncSession) -> None:
        """
        Start the SLA timer. Should be called the moment a ticket gets its
        first assignment. No-op if already started.
        """
        if ticket.sla_status != SLAStatus.not_started:
            logger.debug(f"SLA already started for {ticket.ticket_id} ({ticket.sla_status})")
            return

        cfg = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        hours = _hours_for(ticket.priority, cfg)
        now   = datetime.now(timezone.utc)

        ticket.sla_start_time     = now
        ticket.sla_due_time       = now + timedelta(hours=hours)
        ticket.sla_due_at         = ticket.sla_due_time      # keep legacy in sync
        ticket.sla_status         = SLAStatus.active
        ticket.sla_paused_at      = None
        ticket.sla_paused_seconds = 0

        logger.info(
            f"SLA started for {ticket.ticket_id} | priority={ticket.priority.value} "
            f"| hours={hours} | due={ticket.sla_due_time.isoformat()}"
        )

    @staticmethod
    async def pause(ticket: Ticket, db: AsyncSession) -> None:
        """
        Pause the SLA timer. Records the pause timestamp for later resumption.
        No-op if already paused or not active.
        """
        if ticket.sla_status != SLAStatus.active:
            return

        ticket.sla_status    = SLAStatus.paused
        ticket.sla_paused_at = datetime.now(timezone.utc)
        logger.info(f"SLA paused for {ticket.ticket_id}")

    @staticmethod
    async def resume(ticket: Ticket, db: AsyncSession) -> None:
        """
        Resume a paused SLA timer.
        Extends sla_due_time by the duration spent paused and accumulates
        paused_seconds for auditing.
        """
        if ticket.sla_status != SLAStatus.paused:
            return

        now = datetime.now(timezone.utc)

        if ticket.sla_paused_at and ticket.sla_due_time:
            paused_since  = _ensure_utc(ticket.sla_paused_at)
            pause_delta   = now - paused_since
            pause_secs    = int(pause_delta.total_seconds())

            ticket.sla_due_time       = _ensure_utc(ticket.sla_due_time) + pause_delta
            ticket.sla_due_at         = ticket.sla_due_time              # keep legacy in sync
            ticket.sla_paused_seconds = (ticket.sla_paused_seconds or 0) + pause_secs

        ticket.sla_paused_at = None

        # Re-evaluate: may already be overdue
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
        """
        Stop the SLA — ticket has been resolved or closed.
        Marks sla_status = completed regardless of whether it was on time.
        """
        if ticket.sla_status in (SLAStatus.completed, SLAStatus.not_started):
            return

        ticket.sla_status    = SLAStatus.completed
        ticket.sla_paused_at = None
        logger.info(f"SLA completed for {ticket.ticket_id}")

    @staticmethod
    async def recalculate(ticket: Ticket, db: AsyncSession, new_priority: TicketPriority | None = None) -> None:
        """
        Recalculate sla_due_time after a priority change.
        Preserves the original sla_start_time; only the deadline shifts.
        No-op if SLA has not started yet.
        """
        if ticket.sla_status == SLAStatus.not_started or not ticket.sla_start_time:
            return

        cfg      = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        priority = new_priority or ticket.priority
        hours    = _hours_for(priority, cfg)
        start    = _ensure_utc(ticket.sla_start_time)
        new_due  = start + timedelta(hours=hours)

        # Extend by accumulated pause time so pauses aren't "lost"
        if ticket.sla_paused_seconds:
            new_due += timedelta(seconds=ticket.sla_paused_seconds)

        ticket.sla_due_time = new_due
        ticket.sla_due_at   = new_due  # keep legacy in sync

        # Re-evaluate overdue status
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
        Return seconds remaining until SLA due time.
        Positive  → time remaining.
        Negative  → overdue by that many seconds.
        0         → not started or completed.
        """
        if not ticket.sla_due_time or ticket.sla_status in (
            SLAStatus.not_started, SLAStatus.completed
        ):
            return 0

        due = _ensure_utc(ticket.sla_due_time)

        # If paused: remaining is measured from the pause moment, not now
        if ticket.sla_status == SLAStatus.paused and ticket.sla_paused_at:
            paused_at = _ensure_utc(ticket.sla_paused_at)
            return int((due - paused_at).total_seconds())

        return int((due - datetime.now(timezone.utc)).total_seconds())

    @staticmethod
    def get_overdue_seconds(ticket: Ticket) -> int:
        """Return how many seconds overdue the ticket is (0 if not overdue)."""
        return max(0, -SLAService.get_remaining_seconds(ticket))

    @staticmethod
    def get_status_info(ticket: Ticket) -> dict:
        """
        Return a full SLA status dict suitable for API responses and the frontend.

        Shape:
        {
            "sla_status": "active" | "paused" | "overdue" | "completed" | "not_started",
            "sla_start_time": ISO string | null,
            "sla_due_time":   ISO string | null,
            "sla_remaining_seconds": int (0 if completed/not started),
            "sla_overdue_seconds":   int (0 if not overdue),
            "sla_paused_seconds":    int (total accumulated pause seconds),
            "sla_remaining_display": "2h 15m" | null,
            "sla_overdue_display":   "2h 15m overdue" | null,
            "is_overdue":  bool,
            "is_paused":   bool,
            "is_completed": bool,
        }
        """
        status = ticket.sla_status or SLAStatus.not_started
        remaining = SLAService.get_remaining_seconds(ticket)
        overdue   = SLAService.get_overdue_seconds(ticket)

        # Consider "active" tickets that are past their due time as overdue
        is_overdue = (
            status == SLAStatus.overdue
            or (status == SLAStatus.active and remaining < 0)
        )

        return {
            "sla_status":              status.value,
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

    # ── Convenience: apply SLA changes based on ticket status transition ───

    @staticmethod
    async def handle_status_change(
        ticket: Ticket,
        new_status: TicketStatus,
        old_status: TicketStatus,
        db: AsyncSession,
    ) -> None:
        """
        Apply the correct SLA action for a ticket status transition.
        Called from the PATCH endpoint after validating the new status.
        """
        cfg = (await db.execute(select(SLAConfig).limit(1))).scalar_one_or_none()
        pause_on = _pause_statuses(cfg)

        new_str = new_status.value if hasattr(new_status, "value") else str(new_status)
        old_str = old_status.value if hasattr(old_status, "value") else str(old_status)

        entering_pause  = new_str in pause_on and old_str not in pause_on
        leaving_pause   = old_str in pause_on and new_str not in pause_on
        entering_stop   = new_status in STOP_STATUSES

        if entering_stop:
            await SLAService.stop(ticket, db)
        elif entering_pause:
            await SLAService.pause(ticket, db)
        elif leaving_pause:
            await SLAService.resume(ticket, db)


# ── SLA Breach Detector (background job) ─────────────────────────────────────

class SLABreachDetector:
    """
    Background asyncio task that runs every 60 seconds.
    Finds tickets with sla_status=active whose sla_due_time has passed
    and marks them as overdue.

    Also importable standalone via check_breaches() for on-demand use.
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
        """
        Mark all active tickets whose sla_due_time has elapsed as overdue.
        Returns the number of tickets updated.
        """
        now = datetime.now(timezone.utc)
        updated = 0

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Ticket).where(
                    Ticket.sla_status == SLAStatus.active,
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
                    f"SLA breach check: {updated} ticket(s) newly overdue "
                    f"at {now.isoformat()}"
                )

        return updated


# ── Singleton ─────────────────────────────────────────────────────────────────
sla_breach_detector = SLABreachDetector()
