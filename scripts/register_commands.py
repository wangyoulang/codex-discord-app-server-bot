from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import discord

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_discord_bot.claude.worker import ClaudeWorker
from codex_discord_bot.codex.session_router import SessionRouter
from codex_discord_bot.codex.worker_pool import WorkerPool
from codex_discord_bot.discord.bot import CodexDiscordBot
from codex_discord_bot.logging import configure_logging
from codex_discord_bot.providers.runtime import ProviderWorkerRuntime
from codex_discord_bot.runtime.startup import build_application_context


async def amain() -> None:
    app_state = await build_application_context()
    configure_logging(app_state.settings.log_level)
    app_state.worker_pool = ProviderWorkerRuntime(
        codex_pool=WorkerPool(app_state.settings),
        claude_pool=WorkerPool(
            app_state.settings,
            worker_factory=lambda worker_key: ClaudeWorker(
                app_state.settings,
                worker_key=worker_key,
            ),
        ),
    )
    app_state.session_router = SessionRouter(
        app_state.workspace_service,
        app_state.session_service,
    )

    bot = CodexDiscordBot(app_state)
    await bot.login(app_state.settings.discord_bot_token)
    try:
        guild = None
        if app_state.settings.discord_guild_id is not None:
            guild = discord.Object(id=app_state.settings.discord_guild_id)
        synced = await bot.tree.sync(guild=guild)
        print(f"synced={len(synced)}")
    finally:
        await bot.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
