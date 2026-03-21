from __future__ import annotations

import asyncio

from codex_discord_bot.codex.session_router import SessionRouter
from codex_discord_bot.codex.worker_pool import WorkerPool
from codex_discord_bot.discord.bot import CodexDiscordBot
from codex_discord_bot.logging import configure_logging
from codex_discord_bot.runtime.startup import build_application_context


async def amain() -> None:
    app_state = await build_application_context()
    configure_logging(app_state.settings.log_level)
    app_state.worker_pool = WorkerPool(app_state.settings)
    app_state.session_router = SessionRouter(
        app_state.workspace_service,
        app_state.session_service,
    )

    bot = CodexDiscordBot(app_state)
    try:
        await bot.start(app_state.settings.discord_bot_token)
    finally:
        await bot.close()


def main() -> None:
    asyncio.run(amain())
