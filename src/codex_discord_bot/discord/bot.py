from __future__ import annotations

import discord
from discord.ext import commands

from codex_discord_bot.discord.command_tree import register_commands
from codex_discord_bot.discord.intents import build_intents
from codex_discord_bot.discord.views.session_controls import SessionControlView
from codex_discord_bot.logging import get_logger
from codex_discord_bot.runtime.background_tasks import run_idle_worker_reaper

logger = get_logger(__name__)


class CodexDiscordBot(commands.Bot):
    def __init__(self, app_state) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(),
            application_id=app_state.settings.discord_application_id,
        )
        self.app_state = app_state
        self.session_control_view = SessionControlView(self.app_state)
        self._closed = False

    async def setup_hook(self) -> None:
        register_commands(self)
        self.add_view(self.session_control_view)
        task = self.loop.create_task(run_idle_worker_reaper(self.app_state))
        self.app_state.background_tasks.append(task)

        if self.app_state.settings.discord_sync_guild_commands:
            guild_id = self.app_state.settings.discord_guild_id
            if guild_id is not None:
                await self.tree.sync(guild=discord.Object(id=guild_id))
            else:
                await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info(
            "discord.ready",
            user=str(self.user),
            guild_count=len(self.guilds),
        )

    async def on_message(self, message: discord.Message) -> None:
        from codex_discord_bot.discord.handlers.thread_messages import handle_thread_message

        await handle_thread_message(self, message)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await super().close()
        await self.app_state.close()
