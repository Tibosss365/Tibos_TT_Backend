"""Periodic cleanup of audit_log rows older than 30 days.

Runs once at startup (after a short delay) then every 24 hours.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

logger = logging.getLogger("uvicorn.error")

RETENTION_DAYS = 30
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60   # 24 hours
_STARTUP_DELAY_SECONDS  = 30             # let migrations finish first


class AuditCleanup:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="audit-cleanup")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    # ------------------------------------------------------------------
    async def _loop(self) -> None:
        try:
            await asyncio.sleep(_STARTUP_DELAY_SECONDS)
        except asyncio.CancelledError:
            return

        while self._running:
            try:
                await self._purge()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[audit-cleanup] Error during purge: {exc}")

            try:
                await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _purge(self) -> None:
        from app.database import AsyncSessionLocal
        from app.models.audit_log import AuditLog

        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(AuditLog).where(AuditLog.created_at < cutoff)
            )
            await db.commit()
            deleted = result.rowcount
            if deleted:
                logger.info(
                    f"[audit-cleanup] Purged {deleted} audit log "
                    f"entries older than {RETENTION_DAYS} days"
                )


audit_cleanup = AuditCleanup()
