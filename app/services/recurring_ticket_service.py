"""
Recurring ticket service — creates scheduled tickets based on cron expressions.

Uses croniter to calculate next_run_at.  Checks every minute for due templates.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")

_CHECK_INTERVAL = 60           # seconds
_STARTUP_DELAY  = 120          # seconds


def _compute_next_run(cron_expr: str, after: datetime) -> datetime | None:
    try:
        from croniter import croniter
        it = croniter(cron_expr, after)
        return it.get_next(datetime)
    except Exception as exc:
        logger.warning(f"[recurring] croniter failed for '{cron_expr}': {exc}")
        return None


class RecurringTicketService:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="recurring-ticket-service")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(_STARTUP_DELAY)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[recurring] Error: {exc}")
            try:
                await asyncio.sleep(_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        from app.database import AsyncSessionLocal
        from app.models.feature_models import RecurringTicketTemplate
        from app.models.ticket import Ticket, TicketStatus, TicketPriority

        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(RecurringTicketTemplate).where(
                    RecurringTicketTemplate.is_active == True,
                )
            )
            templates = result.scalars().all()

            for tmpl in templates:
                # First run: compute next_run_at from now
                if tmpl.next_run_at is None:
                    tmpl.next_run_at = _compute_next_run(tmpl.cron_expr, now)
                    continue

                # Ensure timezone-aware comparison
                next_run = tmpl.next_run_at
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)

                if now >= next_run:
                    await self._create_ticket(tmpl, db)
                    tmpl.last_run_at = now
                    tmpl.next_run_at = _compute_next_run(tmpl.cron_expr, now)
                    logger.info(
                        f"[recurring] Created ticket for template '{tmpl.name}'. "
                        f"Next run: {tmpl.next_run_at}"
                    )

            await db.commit()

    async def _create_ticket(self, tmpl, db: AsyncSession) -> None:
        from app.models.ticket import Ticket, TicketPriority, TicketStatus

        try:
            priority = TicketPriority(tmpl.priority)
        except ValueError:
            priority = TicketPriority.medium

        ticket = Ticket(
            subject=tmpl.subject,
            category=tmpl.category,
            priority=priority,
            status=TicketStatus.open,
            description=tmpl.description or f"Auto-created by recurring template: {tmpl.name}",
            submitter_name="System (Recurring)",
            company="",
            contact_name="",
            email="",
            source="portal",
            assignee_id=tmpl.assignee_id,
            group_id=tmpl.group_id,
            ticket_prefix="TKT",
            ticket_number_digits=4,
        )
        db.add(ticket)
        await db.flush()


recurring_ticket_service = RecurringTicketService()
