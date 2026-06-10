"""
Escalation service — periodic background checker.

Every hour it finds open tickets that have exceeded their
`hours_before_escalation` threshold for a matching EscalationRule,
and reassigns / notifies accordingly.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("uvicorn.error")

_CHECK_INTERVAL = 60 * 60      # 1 hour
_STARTUP_DELAY  = 90           # seconds after server start


class EscalationService:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="escalation-service")

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
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[escalation] Error: {exc}")
            try:
                await asyncio.sleep(_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _check(self) -> None:
        from app.database import AsyncSessionLocal
        from app.models.feature_models import EscalationRule
        from app.models.ticket import Ticket, TicketStatus, TicketPriority

        async with AsyncSessionLocal() as db:
            # Load all active escalation rules
            result = await db.execute(
                select(EscalationRule).where(EscalationRule.is_active == True)
            )
            rules = result.scalars().all()

            for rule in rules:
                cutoff = datetime.now(timezone.utc) - timedelta(
                    hours=rule.hours_before_escalation
                )
                # Find matching open tickets older than the threshold
                t_result = await db.execute(
                    select(Ticket).where(
                        Ticket.is_deleted == False,
                        Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]),
                        Ticket.priority == rule.priority,
                        Ticket.created_at <= cutoff,
                    )
                )
                tickets = t_result.scalars().all()

                for ticket in tickets:
                    await self._escalate(ticket, rule, db)

            await db.commit()

    async def _escalate(self, ticket, rule, db) -> None:
        import uuid as _uuid
        from app.services.email_sender import send_email_async

        logger.info(
            f"[escalation] Escalating ticket {ticket.ticket_id} "
            f"via rule '{rule.name}'"
        )

        # Reassign to first user in escalate_to_ids (if any)
        if rule.escalate_to_ids:
            try:
                new_assignee = _uuid.UUID(str(rule.escalate_to_ids[0]))
                ticket.assignee_id = new_assignee
            except (ValueError, IndexError):
                pass

        # Send notification email
        if rule.notify_email:
            try:
                await send_email_async(
                    to_addr=rule.notify_email,
                    subject=f"[ESCALATED] Ticket {ticket.ticket_id} — {ticket.subject}",
                    body=(
                        f"Ticket {ticket.ticket_id} has been escalated via rule '{rule.name}'.\n\n"
                        f"Subject: {ticket.subject}\n"
                        f"Priority: {ticket.priority}\n"
                        f"Status: {ticket.status}\n"
                        f"Created: {ticket.created_at.isoformat()}\n"
                    ),
                )
            except Exception as exc:
                logger.warning(f"[escalation] Email notify failed: {exc}")


escalation_service = EscalationService()
