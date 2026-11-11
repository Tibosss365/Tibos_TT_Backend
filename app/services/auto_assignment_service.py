"""
Auto-assignment service — round-robin across available agents in a group.

Logic:
  1. Find all active technician/admin users in *group_id* (if specified).
  2. Assign to the one with the fewest open tickets (least-loaded).
  3. If no group_id, pick globally among all active agents.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.models.ticket import (
    Ticket, TicketStatus, TicketPriority, TicketTimeline, TimelineType, SLAStatus,
)

logger = logging.getLogger("uvicorn.error")

_CHECK_INTERVAL = 60      # seconds between sweeps
_STARTUP_DELAY = 45       # seconds after server start
_MAX_PER_SWEEP = 25       # safety cap on tickets handled per sweep

# A critical ticket is more work than a low one — weights make "least loaded"
# reflect effort, not just a head-count.
_WEIGHT = {
    TicketPriority.critical: 4,
    TicketPriority.high: 3,
    TicketPriority.medium: 2,
    TicketPriority.low: 1,
}


class AutoAssignService:
    """
    Background sweep: a ticket that is still unassigned `auto_assign_after_minutes`
    after it was created goes to the technician with the lowest current workload.

    Off unless enabled in Admin → Tickets. Only technicians receive tickets
    (admins are managers and are never auto-assigned). If the ticket has a
    group and some technicians belong to it, only they are considered;
    otherwise every active technician is.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="auto-assign-service")

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
                await self.sweep()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[auto-assign] Error: {exc}")
            try:
                await asyncio.sleep(_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break

    async def sweep(self) -> int:
        """One pass. Returns how many tickets were assigned."""
        from app.database import AsyncSessionLocal
        from app.models.admin import TicketSettings

        async with AsyncSessionLocal() as db:
            ts = (await db.execute(select(TicketSettings).limit(1))).scalar_one_or_none()
            if not ts or not ts.auto_assign_enabled:
                return 0
            minutes = max(1, int(ts.auto_assign_after_minutes or 30))
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

            # skip_locked: a ticket someone is editing right now is left alone this round
            tickets = (await db.execute(
                select(Ticket)
                .where(
                    Ticket.is_deleted == False,
                    Ticket.assignee_id.is_(None),
                    Ticket.status == TicketStatus.open,
                    Ticket.created_at <= cutoff,
                )
                .order_by(Ticket.created_at.asc())
                .limit(_MAX_PER_SWEEP)
                .with_for_update(skip_locked=True)
            )).scalars().all()
            if not tickets:
                return 0

            agents = (await db.execute(
                select(User).where(User.is_active == True, User.role == UserRole.technician)
            )).scalars().all()
            if not agents:
                return 0

            load = await _current_load(db)
            assigned = 0
            for ticket in tickets:
                pool = _pool_for(ticket, agents)
                # lowest weighted load first, then fewest tickets, then name (stable)
                pick = min(pool, key=lambda u: (load.get(u.id, (0, 0))[0], load.get(u.id, (0, 0))[1], u.name or ""))
                await _assign(db, ticket, pick)
                w, c = load.get(pick.id, (0, 0))
                load[pick.id] = (w + _WEIGHT.get(ticket.priority, 2), c + 1)
                assigned += 1

            await db.commit()
            logger.info(f"[auto-assign] Assigned {assigned} unpicked ticket(s)")
            return assigned


def _pool_for(ticket, agents: list) -> list:
    """Technicians of the ticket's group when there are any, else all technicians."""
    gid = (ticket.group_id or "").strip().lower()
    if gid:
        same = [u for u in agents if (u.group or "").strip().lower() == gid]
        if same:
            return same
    return agents


async def _current_load(db: AsyncSession) -> dict:
    """{user_id: (weighted_load, ticket_count)} over every unfinished assigned ticket."""
    rows = (await db.execute(
        select(Ticket.assignee_id, Ticket.priority, func.count(Ticket.id))
        .where(
            Ticket.is_deleted == False,
            Ticket.assignee_id.is_not(None),
            Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]),
        )
        .group_by(Ticket.assignee_id, Ticket.priority)
    )).all()
    load: dict = {}
    for uid, prio, cnt in rows:
        w, c = load.get(uid, (0, 0))
        load[uid] = (w + _WEIGHT.get(prio, 2) * cnt, c + cnt)
    return load


async def _assign(db: AsyncSession, ticket, agent) -> None:
    """Same effects as an agent assigning it by hand (in-progress, SLA start, history, notification)."""
    from app.services.sla_service import SLAService
    from app.services.notification_service import notify_ticket_assigned

    now = datetime.now(timezone.utc)
    ticket.assignee_id = agent.id
    ticket.status = TicketStatus.in_progress
    ticket.updated_at = now
    if ticket.sla_status == SLAStatus.not_started:
        await SLAService.start(ticket, db, is_assignment=True, start_time=now)
    db.add(TicketTimeline(
        ticket_id=ticket.id,
        type=TimelineType.assign,
        text=f"Auto-assigned to <strong>{agent.name}</strong> (nobody had picked it up — lowest workload)",
        author_id=None,
    ))
    await notify_ticket_assigned(db, ticket, agent, "Auto-assign")
    logger.info(f"[auto-assign] {ticket.ticket_id} → {agent.name}")


auto_assign_service = AutoAssignService()


async def auto_assign(ticket, db: AsyncSession) -> None:
    """
    Assign *ticket* to the least-loaded available agent.
    Modifies ticket.assignee_id in-place; caller must flush/commit.
    """
    if ticket.assignee_id:
        return  # already assigned

    # Sub-query: open ticket counts per user
    open_counts = (
        select(Ticket.assignee_id, func.count(Ticket.id).label("cnt"))
        .where(
            Ticket.is_deleted == False,
            Ticket.status.not_in([TicketStatus.resolved, TicketStatus.closed]),
            Ticket.assignee_id.is_not(None),
        )
        .group_by(Ticket.assignee_id)
        .subquery()
    )

    # Users query
    users_stmt = (
        select(User, func.coalesce(open_counts.c.cnt, 0).label("load"))
        .outerjoin(open_counts, User.id == open_counts.c.assignee_id)
        .where(
            User.is_active == True,
            User.role.in_([UserRole.technician, UserRole.admin]),
        )
        .order_by(func.coalesce(open_counts.c.cnt, 0).asc(), User.created_at.asc())
    )

    if ticket.group_id:
        users_stmt = users_stmt.where(User.group == ticket.group_id)

    result = await db.execute(users_stmt)
    row = result.first()
    if row:
        user = row[0]
        ticket.assignee_id = user.id
        logger.info(
            f"[auto-assign] Ticket {ticket.ticket_id} → {user.name} (load={row[1]})"
        )
