from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.providers.types import ProviderKind


class DiscordSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, value: DiscordSession) -> DiscordSession:
        self.session.add(value)
        await self.session.flush()
        await self.session.refresh(value)
        return value

    async def get_by_discord_thread_id(self, discord_thread_id: str) -> DiscordSession | None:
        stmt = select(DiscordSession).where(DiscordSession.discord_thread_id == discord_thread_id)
        return await self.session.scalar(stmt)

    async def get_by_provider_thread_id(
        self,
        provider_thread_id: str,
        *,
        provider: ProviderKind,
    ) -> DiscordSession | None:
        stmt = select(DiscordSession).where(
            DiscordSession.codex_thread_id == provider_thread_id,
            DiscordSession.provider == provider,
        )
        return await self.session.scalar(stmt)

    async def update_provider(
        self,
        record: DiscordSession,
        *,
        provider: ProviderKind,
    ) -> DiscordSession:
        record.provider = provider
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def update_codex_thread_id(
        self,
        record: DiscordSession,
        *,
        codex_thread_id: str | None,
    ) -> DiscordSession:
        record.codex_thread_id = codex_thread_id
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def update_status(
        self,
        record: DiscordSession,
        *,
        status: SessionStatus,
        active_turn_id: str | None = None,
        last_bot_message_id: str | None = None,
    ) -> DiscordSession:
        record.status = status
        record.active_turn_id = active_turn_id
        if last_bot_message_id is not None:
            record.last_bot_message_id = last_bot_message_id
        await self.session.flush()
        await self.session.refresh(record)
        return record
