"""
WebSocket connection manager for internal real-time communications.

Supports:
  - Per-user connections (multiple tabs allowed)
  - Broadcast to all connected clients
  - Broadcast to a specific user
  - Send to a specific connection
"""
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class WSConnection:
    ws: WebSocket
    user_id: str
    conn_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class WSManager:
    def __init__(self) -> None:
        # conn_id -> WSConnection
        self._connections: dict[str, WSConnection] = {}
        # user_id -> set of conn_ids
        self._user_index: dict[str, set[str]] = {}

    async def connect(self, ws: WebSocket, user_id: str) -> WSConnection:
        await ws.accept()
        conn = WSConnection(ws=ws, user_id=user_id)
        self._connections[conn.conn_id] = conn
        self._user_index.setdefault(user_id, set()).add(conn.conn_id)
        return conn

    def disconnect(self, conn: WSConnection) -> None:
        self._connections.pop(conn.conn_id, None)
        user_conns = self._user_index.get(conn.user_id, set())
        user_conns.discard(conn.conn_id)
        if not user_conns:
            self._user_index.pop(conn.user_id, None)

    async def send_to(self, conn: WSConnection, payload: dict) -> None:
        try:
            await conn.ws.send_text(json.dumps(payload))
        except Exception:
            self.disconnect(conn)

    async def send_to_user(self, user_id: str, payload: dict) -> None:
        conn_ids = list(self._user_index.get(user_id, set()))
        for cid in conn_ids:
            conn = self._connections.get(cid)
            if conn:
                await self.send_to(conn, payload)

    async def broadcast(self, payload: dict, exclude_user: str | None = None) -> None:
        for conn in list(self._connections.values()):
            if exclude_user and conn.user_id == exclude_user:
                continue
            await self.send_to(conn, payload)

    @property
    def connected_user_ids(self) -> list[str]:
        return list(self._user_index.keys())


# Singleton instance
ws_manager = WSManager()
