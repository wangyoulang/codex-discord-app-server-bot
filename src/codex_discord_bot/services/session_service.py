from __future__ import annotations

from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.repositories.sessions import DiscordSessionRepository
from codex_discord_bot.providers.types import ProviderKind


class SessionService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_session(
        self,
        *,
        discord_thread_id: str,
        workspace_id: int,
        provider: ProviderKind = ProviderKind.codex,
    ) -> DiscordSession:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            existing = await repo.get_by_discord_thread_id(discord_thread_id)
            if existing is not None:
                if existing.provider != provider:
                    if existing.codex_thread_id is not None:
                        raise ValueError("当前线程已绑定其他 provider 会话，请先执行 detach。")
                    existing = await repo.update_provider(existing, provider=provider)
                return existing
            value = DiscordSession(
                discord_thread_id=discord_thread_id,
                workspace_id=workspace_id,
                provider=provider,
                status=SessionStatus.ready,
            )
            return await repo.create(value)

    async def get_session_for_thread(self, discord_thread_id: str) -> DiscordSession | None:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            return await repo.get_by_discord_thread_id(discord_thread_id)

    async def get_session_for_provider_thread(
        self,
        provider_thread_id: str,
        *,
        provider: ProviderKind,
    ) -> DiscordSession | None:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            return await repo.get_by_provider_thread_id(provider_thread_id, provider=provider)

    async def get_session_for_codex_thread(self, codex_thread_id: str) -> DiscordSession | None:
        return await self.get_session_for_provider_thread(
            codex_thread_id,
            provider=ProviderKind.codex,
        )

    async def bind_codex_thread(
        self,
        *,
        discord_thread_id: str,
        codex_thread_id: str | None,
        provider: ProviderKind = ProviderKind.codex,
    ) -> DiscordSession:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            record = await repo.get_by_discord_thread_id(discord_thread_id)
            if record is None:
                raise ValueError("会话不存在，无法绑定 Codex thread")
            if record.provider != provider:
                record = await repo.update_provider(record, provider=provider)
            return await repo.update_codex_thread_id(record, codex_thread_id=codex_thread_id)

    async def detach_codex_thread(
        self,
        *,
        discord_thread_id: str,
    ) -> DiscordSession:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            record = await repo.get_by_discord_thread_id(discord_thread_id)
            if record is None:
                raise ValueError("会话不存在，无法解绑 Codex thread")
            record = await repo.update_codex_thread_id(record, codex_thread_id=None)
            return await repo.update_status(
                record,
                status=SessionStatus.ready,
                active_turn_id=None,
            )

    async def mark_running(
        self,
        *,
        discord_thread_id: str,
        active_turn_id: str | None = None,
        last_bot_message_id: str | None = None,
    ) -> DiscordSession:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            record = await repo.get_by_discord_thread_id(discord_thread_id)
            if record is None:
                raise ValueError("会话不存在，无法更新状态")
            return await repo.update_status(
                record,
                status=SessionStatus.running,
                active_turn_id=active_turn_id,
                last_bot_message_id=last_bot_message_id,
            )

    async def mark_ready(
        self,
        *,
        discord_thread_id: str,
        last_bot_message_id: str | None = None,
    ) -> DiscordSession:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            record = await repo.get_by_discord_thread_id(discord_thread_id)
            if record is None:
                raise ValueError("会话不存在，无法更新状态")
            return await repo.update_status(
                record,
                status=SessionStatus.ready,
                active_turn_id=None,
                last_bot_message_id=last_bot_message_id,
            )

    async def mark_error(
        self,
        *,
        discord_thread_id: str,
        last_bot_message_id: str | None = None,
    ) -> DiscordSession:
        async with self.db.session() as session:
            repo = DiscordSessionRepository(session)
            record = await repo.get_by_discord_thread_id(discord_thread_id)
            if record is None:
                raise ValueError("会话不存在，无法更新状态")
            return await repo.update_status(
                record,
                status=SessionStatus.error,
                active_turn_id=None,
                last_bot_message_id=last_bot_message_id,
            )
