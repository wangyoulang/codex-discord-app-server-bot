from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.persistence.repositories.workspaces import WorkspaceRepository
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.services.session_service import SessionService


def test_session_service_tracks_active_turn_lifecycle(tmp_path: Path) -> None:
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

        service = SessionService(db)
        created = await service.ensure_session(
            discord_thread_id="discord_thread_1",
            workspace_id=workspace.id,
        )
        assert created.status == SessionStatus.ready
        assert created.active_turn_id is None

        running = await service.mark_running(
            discord_thread_id="discord_thread_1",
            active_turn_id="turn_1",
            last_bot_message_id="msg_1",
        )
        assert running.status == SessionStatus.running
        assert running.active_turn_id == "turn_1"
        assert running.last_bot_message_id == "msg_1"

        ready = await service.mark_ready(
            discord_thread_id="discord_thread_1",
            last_bot_message_id="msg_2",
        )
        assert ready.status == SessionStatus.ready
        assert ready.active_turn_id is None
        assert ready.last_bot_message_id == "msg_2"

        errored = await service.mark_error(
            discord_thread_id="discord_thread_1",
            last_bot_message_id="msg_3",
        )
        assert errored.status == SessionStatus.error
        assert errored.active_turn_id is None
        assert errored.last_bot_message_id == "msg_3"

        await db.close()

    asyncio.run(scenario())
