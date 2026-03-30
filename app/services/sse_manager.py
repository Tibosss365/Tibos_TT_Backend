"""
SSE (Server-Sent Events) manager.

Each connected user gets an asyncio.Queue. When a ticket event happens,
the event is broadcast to all relevant queues so SSE streams can push it
to connected browser clients in real time.
"""
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import AsyncGenerator


@dataclass
class SSEEvent:
    event: str  # e.g. "ticket_created", "ticket_updated", "notification"
    data: dict
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class SSEManager:
    def __init__(self) -> None:
        # user_id (str) -> set of queues (one per browser tab/connection)
        self._queues: dict[str, set[asyncio.Queue]] = {}

    def _get_or_create(self, user_id: str) -> set[asyncio.Queue]:
        if user_id not in self._queues:
            self._queues[user_id] = set()
        return self._queues[user_id]

    async def subscribe(self, user_id: str) -> tuple[asyncio.Queue, "SSEManager"]:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._get_or_create(user_id).add(q)
        return q, self

    def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        queues = self._queues.get(user_id, set())
        queues.discard(q)
        if not queues:
            self._queues.pop(user_id, None)

    async def broadcast_to_user(self, user_id: str, event: SSEEvent) -> None:
        queues = self._queues.get(user_id, set())
        dead: list[asyncio.Queue] = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            queues.discard(q)

    async def broadcast_to_all(self, event: SSEEvent) -> None:
        for user_id in list(self._queues.keys()):
            await self.broadcast_to_user(user_id, event)

    async def stream(self, user_id: str, q: asyncio.Queue) -> AsyncGenerator[str, None]:
        """Yields SSE-formatted strings for the response generator."""
        # Send a connected heartbeat
        yield f"event: connected\ndata: {json.dumps({'user_id': user_id})}\n\n"
        try:
            while True:
                try:
                    event: SSEEvent = await asyncio.wait_for(q.get(), timeout=30.0)
                    payload = json.dumps(event.data)
                    yield f"id: {event.id}\nevent: {event.event}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment so connection stays alive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            self.unsubscribe(user_id, q)


# Singleton instance used across the application
sse_manager = SSEManager()
