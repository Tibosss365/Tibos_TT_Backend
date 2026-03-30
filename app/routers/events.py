"""
SSE endpoint — GET /events

The client connects with a Bearer token in the Authorization header
(or via query param ?token=... for EventSource which can't set headers).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.security import decode_token
from app.database import get_db
from app.models.user import User
from app.services.sse_manager import sse_manager

router = APIRouter(prefix="/events", tags=["sse"])


@router.get("")
async def sse_stream(
    token: str = Query(..., description="JWT access token"),
    db: AsyncSession = Depends(get_db),
):
    # Validate token manually (EventSource API can't set headers)
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            raise ValueError("Missing subject")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    q, manager = await sse_manager.subscribe(user_id)

    return StreamingResponse(
        manager.stream(user_id, q),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering
            "Connection": "keep-alive",
        },
    )
