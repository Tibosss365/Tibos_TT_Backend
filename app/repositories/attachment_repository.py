"""
Repository layer for TicketAttachment.

All DB access for attachments goes through this class.
Callers pass an AsyncSession; this keeps session lifetime management
out of the repository (the service layer owns the transaction).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket_attachment import TicketAttachment


@dataclass
class AttachmentCreate:
    ticket_id: uuid.UUID
    filename: str
    content_type: str
    size: int
    storage_key: str
    storage_url: str
    is_inline: bool = False


class AttachmentRepository:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, data: AttachmentCreate) -> TicketAttachment:
        att = TicketAttachment(
            ticket_id=data.ticket_id,
            filename=data.filename,
            content_type=data.content_type,
            size=data.size,
            storage_key=data.storage_key,
            storage_url=data.storage_url,
            is_inline=data.is_inline,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(att)
        await self._db.flush()
        return att

    async def get_by_ticket(self, ticket_id: uuid.UUID) -> list[TicketAttachment]:
        result = await self._db.execute(
            select(TicketAttachment)
            .where(TicketAttachment.ticket_id == ticket_id)
            .order_by(TicketAttachment.created_at)
        )
        return list(result.scalars().all())

    async def get_by_id(self, attachment_id: uuid.UUID) -> TicketAttachment | None:
        result = await self._db.execute(
            select(TicketAttachment).where(TicketAttachment.id == attachment_id)
        )
        return result.scalar_one_or_none()

    async def exists(
        self, ticket_id: uuid.UUID, filename: str, size: int
    ) -> bool:
        """Idempotency check: has this exact attachment already been saved?"""
        result = await self._db.execute(
            select(TicketAttachment.id).where(
                TicketAttachment.ticket_id == ticket_id,
                TicketAttachment.filename == filename,
                TicketAttachment.size == size,
            )
        )
        return result.scalar_one_or_none() is not None

    async def delete_by_id(self, attachment_id: uuid.UUID) -> None:
        await self._db.execute(
            delete(TicketAttachment).where(TicketAttachment.id == attachment_id)
        )
        await self._db.flush()

    async def delete_by_ticket(self, ticket_id: uuid.UUID) -> list[str]:
        """Delete all attachments for a ticket. Returns their storage_keys."""
        result = await self._db.execute(
            select(TicketAttachment.storage_key).where(
                TicketAttachment.ticket_id == ticket_id
            )
        )
        keys = [row[0] for row in result.all() if row[0]]
        await self._db.execute(
            delete(TicketAttachment).where(
                TicketAttachment.ticket_id == ticket_id
            )
        )
        await self._db.flush()
        return keys
