"""
WebSocket endpoint — WS /ws?token=<jwt>

Used for bidirectional internal communications:
  - Agents can send messages to specific users or broadcast
  - Server pushes live ticket/notification events
"""
import json
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.security import decode_token
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.ws_manager import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
):
    # Authenticate
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        if not user_id:
            raise ValueError()
    except Exception:
        await websocket.close(code=4001)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user: User | None = result.scalar_one_or_none()

    if not user or not user.is_active:
        await websocket.close(code=4003)
        return

    conn = await ws_manager.connect(websocket, user_id)

    # Notify the user they're connected
    await ws_manager.send_to(conn, {
        "type": "connected",
        "user_id": user_id,
        "name": user.name,
    })

    # Notify others this user came online
    await ws_manager.broadcast(
        {"type": "user_online", "user_id": user_id, "name": user.name},
        exclude_user=user_id,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await ws_manager.send_to(conn, {"type": "pong"})

            elif msg_type == "message":
                # Agent-to-agent or broadcast chat message
                target_user_id = msg.get("to")
                payload = {
                    "type": "message",
                    "from_user_id": user_id,
                    "from_name": user.name,
                    "text": msg.get("text", ""),
                }
                if target_user_id:
                    await ws_manager.send_to_user(target_user_id, payload)
                    await ws_manager.send_to(conn, payload)  # echo to sender
                else:
                    await ws_manager.broadcast(payload)

            elif msg_type == "typing":
                await ws_manager.broadcast(
                    {"type": "typing", "user_id": user_id, "name": user.name},
                    exclude_user=user_id,
                )

    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(conn)
        await ws_manager.broadcast(
            {"type": "user_offline", "user_id": user_id, "name": user.name},
            exclude_user=user_id,
        )
