from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.models import Workspace


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, workspace: Workspace) -> Workspace:
        self.session.add(workspace)
        await self.session.flush()
        await self.session.refresh(workspace)
        return workspace

    async def get_by_forum_channel(self, guild_id: str, forum_channel_id: str) -> Workspace | None:
        stmt = select(Workspace).where(
            Workspace.guild_id == guild_id,
            Workspace.forum_channel_id == forum_channel_id,
        )
        return await self.session.scalar(stmt)

    async def list_by_guild(self, guild_id: str) -> list[Workspace]:
        stmt = select(Workspace).where(Workspace.guild_id == guild_id).order_by(Workspace.name.asc())
        result = await self.session.scalars(stmt)
        return list(result.all())
