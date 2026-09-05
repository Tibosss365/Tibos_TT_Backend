"""
Escalation service — periodic background checker.

Every hour it finds open tickets whose age has crossed the next tier of a
matching EscalationRule's ladder (`levels`, ordered by hours ascending) and
fires that tier — reassign + notify. Ticket.escalation_level tracks how far
a ticket has already climbed so a tier only fires once; it climbs further
tiers on later checks as the ticket keeps sitting unresolved, and resets to
0 if the ticket is reopened after being resolved/closed.
"""
import asyncio
import logging
from datetime import datetime, timezone

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
        from app.models.ticket import Ticket, TicketStatus

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(EscalationRule).where(EscalationRule.is_active == True)
            )
            rules = result.scalars().all()
            now = datetime.now(timezone.utc)

            for rule in rules:
                levels = sorted((rule.levels or []), key=lambda lv: lv.get("hours", 0))
                if not levels:
                    continue

                t_result = await db.execute(
                    select(Ticket).where(
                        Ticket.is_deleted == False,
                        Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]),
                        Ticket.priority == rule.priority,
                    )
                )
                tickets = t_result.scalars().all()

                for ticket in tickets:
                    created = ticket.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    elapsed_hours = (now - created).total_seconds() / 3600

                    # Highest tier whose threshold has already passed.
                    target_level = 0
                    for idx, lvl in enumerate(levels, start=1):
                        if elapsed_hours >= lvl.get("hours", 0):
                            target_level = idx

                    if target_level > (ticket.escalation_level or 0):
                        await self._escalate(ticket, rule, levels[target_level - 1], target_level, now, db)

            await db.commit()

    async def _escalate(self, ticket, rule, level_cfg: dict, level_num: int, now: datetime, db) -> None:
        import uuid as _uuid
        from app.services.email_sender import send_email_async

        logger.info(
            f"[escalation] Ticket {ticket.ticket_id} reached tier {level_num} "
            f"of rule '{rule.name}'"
        )

        escalate_to_ids = level_cfg.get("escalate_to_ids") or []
        if escalate_to_ids:
            try:
                ticket.assignee_id = _uuid.UUID(str(escalate_to_ids[0]))
            except (ValueError, IndexError):
                pass

        notify_email = level_cfg.get("notify_email")
        if notify_email:
            try:
                await send_email_async(
                    to_addr=notify_email,
                    subject=f"[ESCALATED — Tier {level_num}] Ticket {ticket.ticket_id} — {ticket.subject}",
                    body=(
                        f"Ticket {ticket.ticket_id} has reached escalation tier {level_num} "
                        f"of rule '{rule.name}' ({level_cfg.get('hours')}h threshold).\n\n"
                        f"Subject: {ticket.subject}\n"
                        f"Priority: {ticket.priority}\n"
                        f"Status: {ticket.status}\n"
                        f"Created: {ticket.created_at.isoformat()}\n"
                    ),
                )
            except Exception as exc:
                logger.warning(f"[escalation] Email notify failed: {exc}")

        ticket.escalation_level = level_num
        ticket.last_escalated_at = now


escalation_service = EscalationService()
