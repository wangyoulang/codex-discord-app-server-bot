from __future__ import annotations

from dataclasses import dataclass

import discord

from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.services.session_service import SessionService
from codex_discord_bot.services.workspace_service import WorkspaceService


@dataclass(slots=True)
class SessionRouteContext:
    workspace: Workspace
    session: DiscordSession | None


class SessionRouter:
    def __init__(
        self,
        workspace_service: WorkspaceService,
        session_service: SessionService,
    ) -> None:
        self.workspace_service = workspace_service
        self.session_service = session_service

    async def ensure_route_for_thread(self, thread: discord.Thread) -> SessionRouteContext:
        return await self.ensure_route_for_provider_thread(thread, provider=None)

    async def ensure_route_for_provider_thread(
        self,
        thread: discord.Thread,
        *,
        provider: ProviderKind | None,
    ) -> SessionRouteContext:
        if thread.guild is None or thread.parent_id is None:
            raise ValueError("当前线程不属于有效 guild/forum")

        workspace = await self.workspace_service.get_workspace_for_forum(
            guild_id=str(thread.guild.id),
            forum_channel_id=str(thread.parent_id),
        )
        if workspace is None:
            raise ValueError("当前论坛频道尚未注册为工作区")

        session = await self.session_service.get_session_for_thread(str(thread.id))
        if provider is not None and (
            session is None or session.provider != provider or session.workspace_id != workspace.id
        ):
            session = await self.session_service.ensure_session(
                discord_thread_id=str(thread.id),
                workspace_id=workspace.id,
                provider=provider,
            )
        return SessionRouteContext(workspace=workspace, session=session)
