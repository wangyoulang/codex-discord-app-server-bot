from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.models import DiscordTurnOutput
from codex_discord_bot.providers.types import ProviderKind


class DiscordTurnOutputRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, value: DiscordTurnOutput) -> DiscordTurnOutput:
        self.session.add(value)
        await self.session.flush()
        await self.session.refresh(value)
        return value

    async def get_by_turn_id(self, codex_turn_id: str) -> DiscordTurnOutput | None:
        stmt = select(DiscordTurnOutput).where(DiscordTurnOutput.codex_turn_id == codex_turn_id)
        return await self.session.scalar(stmt)

    async def get_latest_for_thread(
        self,
        discord_thread_id: str,
        *,
        provider: ProviderKind | None = None,
    ) -> DiscordTurnOutput | None:
        stmt = (
            select(DiscordTurnOutput)
            .where(DiscordTurnOutput.discord_thread_id == discord_thread_id)
            .order_by(desc(DiscordTurnOutput.created_at), desc(DiscordTurnOutput.id))
        )
        if provider is not None:
            stmt = stmt.where(DiscordTurnOutput.provider == provider)
        return await self.session.scalar(stmt)

    async def save(self, record: DiscordTurnOutput) -> DiscordTurnOutput:
        await self.session.flush()
        await self.session.refresh(record)
        return record
