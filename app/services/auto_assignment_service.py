"""
Auto-assignment service — round-robin across available agents in a group.

Logic:
  1. Find all active technician/admin users in *group_id* (if specified).
  2. Assign to the one with the fewest open tickets (least-loaded).
  3. If no group_id, pick globally among all active agents.
"""
import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.models.ticket import Ticket, TicketStatus

logger = logging.getLogger("uvicorn.error")


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
