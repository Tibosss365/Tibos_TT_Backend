"""
First Response Time (FRT) service.

Stamps `first_responded_at` on a ticket the first time a non-requester
comment is posted (i.e. the first agent/tech response).
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")


async def maybe_stamp_first_response(ticket, actor_id, db) -> None:
    """
    If *ticket* has no first_responded_at yet, and *actor_id* is not the
    ticket requester, record the current UTC timestamp.

    Should be called after every new timeline comment is added.
    """
    if ticket.first_responded_at is not None:
        return  # already stamped

    if actor_id is None:
        return

    # If the commenter is the same person who raised the ticket, skip
    if ticket.requester_id and str(actor_id) == str(ticket.requester_id):
        return

    ticket.first_responded_at = datetime.now(timezone.utc)
    # Note: caller is responsible for db.commit() / db.flush()
    logger.debug(
        f"[FRT] Ticket {ticket.ticket_id} first_responded_at stamped to "
        f"{ticket.first_responded_at}"
    )
