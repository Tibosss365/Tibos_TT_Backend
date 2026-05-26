"""Periodic hard-delete of tickets soft-deleted for more than 30 days.

Runs once at startup (after a short delay) then every 24 hours.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

logger = logging.getLogger("uvicorn.error")

RETENTION_DAYS = 30
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_STARTUP_DELAY_SECONDS = 60


class TrashCleanup:
    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="trash-cleanup")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

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
                logger.error(f"[trash-cleanup] Error during purge: {exc}")

            try:
                await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _purge(self) -> None:
        from app.database import AsyncSessionLocal
        from app.models.ticket import Ticket

        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                delete(Ticket).where(
                    Ticket.is_deleted == True,  # noqa: E712
                    Ticket.deleted_at < cutoff,
                )
            )
            await db.commit()
            purged = result.rowcount
            if purged:
                logger.info(
                    f"[trash-cleanup] Permanently deleted {purged} ticket(s) "
                    f"from trash (>{RETENTION_DAYS} days old)"
                )


trash_cleanup = TrashCleanup()
