from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.persistence.models import DiscordSession


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

    async def get_by_codex_thread_id(self, codex_thread_id: str) -> DiscordSession | None:
        stmt = select(DiscordSession).where(DiscordSession.codex_thread_id == codex_thread_id)
        return await self.session.scalar(stmt)

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
        clear_last_bot_message_id: bool = False,
    ) -> DiscordSession:
        record.status = status
        record.active_turn_id = active_turn_id
        if clear_last_bot_message_id:
            record.last_bot_message_id = None
        elif last_bot_message_id is not None:
            record.last_bot_message_id = last_bot_message_id
        await self.session.flush()
        await self.session.refresh(record)
        return record
