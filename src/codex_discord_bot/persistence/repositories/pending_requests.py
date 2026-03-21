from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy import select
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

    async def delete_by_request_id(self, request_id: str) -> None:
        stmt = delete(PendingRequest).where(PendingRequest.request_id == request_id)
        await self.session.execute(stmt)
