from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.models import PendingRequest


class PendingRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, value: PendingRequest) -> PendingRequest:
        self.session.add(value)
        await self.session.flush()
        await self.session.refresh(value)
        return value

    async def get_by_request_id(self, request_id: str) -> PendingRequest | None:
        stmt = select(PendingRequest).where(PendingRequest.request_id == request_id)
        return await self.session.scalar(stmt)

    async def update_message_id(self, request_id: str, message_id: str) -> None:
        stmt = (
            update(PendingRequest)
            .where(PendingRequest.request_id == request_id)
            .values(message_id=message_id)
        )
        await self.session.execute(stmt)

    async def delete_by_request_id(self, request_id: str) -> None:
        stmt = delete(PendingRequest).where(PendingRequest.request_id == request_id)
        await self.session.execute(stmt)
