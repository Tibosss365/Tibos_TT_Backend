"""Record API request outcomes for the staff activity log."""
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.security import decode_token
from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.user import User
from sqlalchemy import select

logger = logging.getLogger("uvicorn.error")


class RequestAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/activity/requests" and request.method != "OPTIONS":
            try:
                response = await call_next(request)
                status_code = response.status_code
            except Exception:
                await self._record(request, 500)
                raise
            await self._record(request, status_code)
            return response
        return await call_next(request)

    async def _record(self, request: Request, status_code: int) -> None:
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return

        user_id = None
        name = email = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                subject = decode_token(auth[7:].strip()).get("sub")
                user_id = uuid.UUID(subject) if subject else None
            except (ValueError, TypeError, AttributeError):
                user_id = None

        try:
            async with AsyncSessionLocal() as db:
                if user_id:
                    user = await db.get(User, user_id)
                    if user:
                        name, email = user.name, user.username
                    else:
                        user_id = None
                client = request.client
                db.add(AuditLog(
                    user_id=user_id,
                    user_name=name,
                    user_email=email,
                    method=request.method,
                    path=request.url.path[:500],
                    status_code=status_code,
                    ip_address=client.host if client else None,
                ))
                await db.commit()
        except Exception:
            logger.exception("Could not persist request audit entry")
