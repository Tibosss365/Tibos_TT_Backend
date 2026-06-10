"""
Public CSAT survey endpoint — no authentication required.

GET  /csat/{token}       → return ticket info for survey display
POST /csat/{token}       → submit rating + comment
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ticket import Ticket
from app.schemas.feature_schemas import CsatSubmitRequest, CsatOut

router = APIRouter(prefix="/csat", tags=["csat"])


async def _get_ticket_by_token(token: str, db: AsyncSession) -> Ticket:
    result = await db.execute(
        select(Ticket).where(Ticket.csat_token == token, Ticket.is_deleted == False)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Survey link not found or expired")
    return ticket


@router.get("/{token}")
async def get_csat_survey(token: str, db: AsyncSession = Depends(get_db)):
    """Return minimal ticket info needed to render the survey page."""
    ticket = await _get_ticket_by_token(token, db)
    return {
        "ticket_id": str(ticket.id),
        "ticket_display_id": ticket.ticket_id,
        "subject": ticket.subject,
        "already_submitted": ticket.csat_rating is not None,
    }


@router.post("/{token}", response_model=CsatOut)
async def submit_csat_survey(
    token: str,
    body: CsatSubmitRequest,
    db: AsyncSession = Depends(get_db),
):
    ticket = await _get_ticket_by_token(token, db)

    if ticket.csat_rating is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Survey already submitted",
        )

    ticket.csat_rating = body.rating
    ticket.csat_comment = body.comment
    await db.commit()

    return CsatOut(
        ticket_id=ticket.id,
        rating=body.rating,
        comment=body.comment,
        submitted_at=datetime.now(timezone.utc).isoformat(),
    )
