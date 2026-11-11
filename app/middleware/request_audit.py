"""Record API request outcomes for the staff activity log."""
import asyncio
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.security import decode_token
from app.database import AsyncSessionAutocommit
from app.models.audit_log import AuditLog
from app.models.user import User
from sqlalchemy import select

logger = logging.getLogger("uvicorn.error")

# Strong references so background tasks are not garbage-collected mid-flight.
_pending: set[asyncio.Task] = set()


class RequestAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/activity/requests" and request.method != "OPTIONS":
            try:
                response = await call_next(request)
                status_code = response.status_code
            except Exception:
                self._record_later(request, 500)
                raise
            self._record_later(request, status_code)
            return response
        return await call_next(request)

    def _record_later(self, request: Request, status_code: int) -> None:
        """Persist the audit row after the response — never block the request on it."""
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return
        client = request.client
        task = asyncio.create_task(self._record(
            method=request.method,
            path=request.url.path[:500],
            auth=request.headers.get("authorization", ""),
            ip=client.host if client else None,
            status_code=status_code,
        ))
        _pending.add(task)
        task.add_done_callback(_pending.discard)

    async def _record(self, *, method: str, path: str, auth: str, ip: str | None, status_code: int) -> None:
        user_id = None
        name = email = None
        if auth.lower().startswith("bearer "):
            try:
                subject = decode_token(auth[7:].strip()).get("sub")
                user_id = uuid.UUID(subject) if subject else None
            except (ValueError, TypeError, AttributeError):
                user_id = None

        try:
            async with AsyncSessionAutocommit() as db:
                if user_id:
                    user = await db.get(User, user_id)
                    if user:
                        name, email = user.name, user.username
                    else:
                        user_id = None
                db.add(AuditLog(
                    user_id=user_id,
                    user_name=name,
                    user_email=email,
                    method=method,
                    path=path,
                    status_code=status_code,
                    ip_address=ip,
                ))
                await db.flush()
        except Exception:
            logger.exception("Could not persist request audit entry")
