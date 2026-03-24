from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.persistence.repositories.workspaces import WorkspaceRepository
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.services.codex_thread_service import CodexThreadService


def test_codex_thread_service_syncs_remote_thread_payload(tmp_path: Path) -> None:
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

        service = CodexThreadService(db)
        record = await service.sync_thread_from_payload(
            workspace_id=workspace.id,
            archived=False,
            thread_payload={
                "id": "codex_thr_1",
                "source": {"custom": "discord-bot"},
                "preview": "继续分析会话恢复",
                "status": {"type": "idle"},
                "createdAt": 1711270800,
                "updatedAt": 1711271100,
            },
        )

        assert record.codex_thread_id == "codex_thr_1"
        assert record.source_kind == "custom"
        assert record.source_label == "discord-bot"
        assert record.preview == "继续分析会话恢复"
        assert record.thread_status == "idle"
        assert record.archived is False
        assert record.thread_created_at is not None
        assert record.thread_updated_at is not None

        workspace_records = await service.list_for_workspace(
            workspace_id=workspace.id,
            scope="workspace",
            archived=False,
            limit=10,
        )
        bot_records = await service.list_for_workspace(
            workspace_id=workspace.id,
            scope="bot",
            archived=False,
            limit=10,
        )
        assert [item.codex_thread_id for item in workspace_records] == ["codex_thr_1"]
        assert [item.codex_thread_id for item in bot_records] == ["codex_thr_1"]

        await db.close()

    asyncio.run(scenario())


def test_codex_thread_service_rejects_cross_thread_binding_takeover(tmp_path: Path) -> None:
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

        service = CodexThreadService(db)
        await service.sync_thread_from_payload(
            workspace_id=workspace.id,
            archived=False,
            thread_payload={
                "id": "codex_thr_2",
                "source": "cli",
                "preview": "CLI 会话",
                "status": {"type": "idle"},
                "createdAt": 1711270800,
                "updatedAt": 1711271100,
            },
        )
        first_bind = await service.ensure_thread_available_for_discord(
            workspace_id=workspace.id,
            codex_thread_id="codex_thr_2",
            discord_thread_id="discord_thread_1",
        )
        assert first_bind.bound_discord_thread_id == "discord_thread_1"

        try:
            await service.ensure_thread_available_for_discord(
                workspace_id=workspace.id,
                codex_thread_id="codex_thr_2",
                discord_thread_id="discord_thread_2",
            )
        except ValueError as exc:
            assert "默认不允许跨线程接管" in str(exc)
        else:
            raise AssertionError("应拒绝另一个 Discord 线程直接接管会话")

        released = await service.release_binding_if_owned(
            codex_thread_id="codex_thr_2",
            discord_thread_id="discord_thread_1",
        )
        assert released is not None
        assert released.bound_discord_thread_id is None

        rebound = await service.ensure_thread_available_for_discord(
            workspace_id=workspace.id,
            codex_thread_id="codex_thr_2",
            discord_thread_id="discord_thread_2",
        )
        assert rebound.bound_discord_thread_id == "discord_thread_2"

        await db.close()

    asyncio.run(scenario())
