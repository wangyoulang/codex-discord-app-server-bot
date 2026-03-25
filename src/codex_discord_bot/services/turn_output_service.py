from __future__ import annotations

from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.enums import TurnOutputState
from codex_discord_bot.persistence.models import DiscordTurnOutput
from codex_discord_bot.persistence.repositories.turn_outputs import DiscordTurnOutputRepository
from codex_discord_bot.providers.types import ProviderKind


class TurnOutputService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def start_turn(
        self,
        *,
        discord_thread_id: str,
        provider: ProviderKind = ProviderKind.codex,
        codex_thread_id: str | None,
        codex_turn_id: str,
        control_message_id: str | None,
    ) -> DiscordTurnOutput:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            existing = await repo.get_by_turn_id(codex_turn_id)
            if existing is not None:
                existing.discord_thread_id = discord_thread_id
                existing.codex_thread_id = codex_thread_id
                existing.control_message_id = control_message_id
                return await repo.save(existing)

            value = DiscordTurnOutput(
                discord_thread_id=discord_thread_id,
                provider=provider,
                codex_thread_id=codex_thread_id,
                codex_turn_id=codex_turn_id,
                control_message_id=control_message_id,
                preview_message_ids_json=[],
                final_message_ids_json=[],
                state=TurnOutputState.pending,
            )
            return await repo.create(value)

    async def get_latest_for_thread(
        self,
        discord_thread_id: str,
        *,
        provider: ProviderKind | None = None,
    ) -> DiscordTurnOutput | None:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            return await repo.get_latest_for_thread(discord_thread_id, provider=provider)

    async def get_by_turn_id(self, codex_turn_id: str) -> DiscordTurnOutput | None:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            return await repo.get_by_turn_id(codex_turn_id)

    async def bind_control_message(self, *, codex_turn_id: str, control_message_id: str) -> DiscordTurnOutput:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            record = await repo.get_by_turn_id(codex_turn_id)
            if record is None:
                raise ValueError("turn 输出记录不存在，无法绑定控制消息")
            record.control_message_id = control_message_id
            return await repo.save(record)

    async def set_preview_message_ids(
        self,
        *,
        codex_turn_id: str,
        preview_message_ids: list[str],
    ) -> DiscordTurnOutput:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            record = await repo.get_by_turn_id(codex_turn_id)
            if record is None:
                raise ValueError("turn 输出记录不存在，无法写入预览消息")
            record.preview_message_ids_json = preview_message_ids
            return await repo.save(record)

    async def set_final_message_ids(
        self,
        *,
        codex_turn_id: str,
        final_message_ids: list[str],
    ) -> DiscordTurnOutput:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            record = await repo.get_by_turn_id(codex_turn_id)
            if record is None:
                raise ValueError("turn 输出记录不存在，无法写入最终消息")
            record.final_message_ids_json = final_message_ids
            return await repo.save(record)

    async def set_active_agent_item(
        self,
        *,
        codex_turn_id: str,
        active_agent_item_id: str | None,
    ) -> DiscordTurnOutput:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            record = await repo.get_by_turn_id(codex_turn_id)
            if record is None:
                raise ValueError("turn 输出记录不存在，无法更新 active_agent_item_id")
            record.active_agent_item_id = active_agent_item_id
            return await repo.save(record)

    async def set_state(
        self,
        *,
        codex_turn_id: str,
        state: TurnOutputState,
        error_text: str | None = None,
    ) -> DiscordTurnOutput:
        async with self.db.session() as session:
            repo = DiscordTurnOutputRepository(session)
            record = await repo.get_by_turn_id(codex_turn_id)
            if record is None:
                raise ValueError("turn 输出记录不存在，无法更新状态")
            record.state = state
            record.error_text = error_text
            return await repo.save(record)
