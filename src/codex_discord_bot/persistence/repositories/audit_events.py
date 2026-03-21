from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.models import AuditEvent


class AuditEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, value: AuditEvent) -> AuditEvent:
        self.session.add(value)
        await self.session.flush()
        await self.session.refresh(value)
        return value
