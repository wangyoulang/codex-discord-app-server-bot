from __future__ import annotations

from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.persistence.repositories.workspaces import WorkspaceRepository


class WorkspaceService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_workspace(
        self,
        *,
        guild_id: str,
        forum_channel_id: str,
        name: str,
        cwd: str,
    ) -> Workspace:
        async with self.db.session() as session:
            repo = WorkspaceRepository(session)
            existing = await repo.get_by_forum_channel(guild_id, forum_channel_id)
            if existing is not None:
                raise ValueError("该论坛频道已经绑定工作区")

            workspace = Workspace(
                guild_id=guild_id,
                forum_channel_id=forum_channel_id,
                name=name,
                cwd=cwd,
            )
            return await repo.create(workspace)

    async def get_workspace_for_forum(
        self,
        *,
        guild_id: str,
        forum_channel_id: str,
    ) -> Workspace | None:
        async with self.db.session() as session:
            repo = WorkspaceRepository(session)
            return await repo.get_by_forum_channel(guild_id, forum_channel_id)

    async def list_workspaces(self, *, guild_id: str) -> list[Workspace]:
        async with self.db.session() as session:
            repo = WorkspaceRepository(session)
            return await repo.list_by_guild(guild_id)
