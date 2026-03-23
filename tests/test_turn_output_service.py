from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.persistence.enums import TurnOutputState
from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.models import DiscordTurnOutput
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.persistence.repositories.workspaces import WorkspaceRepository
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.services.session_service import SessionService
from codex_discord_bot.services.turn_output_service import TurnOutputService


def test_turn_output_service_tracks_preview_and_final_messages(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        async with db.session() as session:
            repo = WorkspaceRepository(session)
            workspace = await repo.create(
                Workspace(
                    guild_id="guild_1",
                    forum_channel_id="forum_1",
                    name="demo",
                    cwd="/repo",
                )
            )

        session_service = SessionService(db)
        await session_service.ensure_session(
            discord_thread_id="discord_thread_1",
            workspace_id=workspace.id,
        )

        service = TurnOutputService(db)
        created = await service.start_turn(
            discord_thread_id="discord_thread_1",
            codex_thread_id="codex_thread_1",
            codex_turn_id="turn_1",
            control_message_id="control_1",
        )
        assert created.state == TurnOutputState.pending
        assert created.preview_message_ids_json == []
        assert created.final_message_ids_json == []

        previewing = await service.set_preview_message_ids(
            codex_turn_id="turn_1",
            preview_message_ids=["preview_1", "preview_2"],
        )
        assert previewing.preview_message_ids_json == ["preview_1", "preview_2"]

        active_item = await service.set_active_agent_item(
            codex_turn_id="turn_1",
            active_agent_item_id="item_1",
        )
        assert active_item.active_agent_item_id == "item_1"

        completed = await service.set_state(
            codex_turn_id="turn_1",
            state=TurnOutputState.completed,
        )
        assert completed.state == TurnOutputState.completed

        finalized = await service.set_final_message_ids(
            codex_turn_id="turn_1",
            final_message_ids=["final_1", "final_2"],
        )
        assert finalized.final_message_ids_json == ["final_1", "final_2"]

        latest = await service.get_latest_for_thread("discord_thread_1")
        assert isinstance(latest, DiscordTurnOutput)
        assert latest.codex_turn_id == "turn_1"
        assert latest.control_message_id == "control_1"
        assert latest.preview_message_ids_json == ["preview_1", "preview_2"]
        assert latest.final_message_ids_json == ["final_1", "final_2"]

        await db.close()

    asyncio.run(scenario())
