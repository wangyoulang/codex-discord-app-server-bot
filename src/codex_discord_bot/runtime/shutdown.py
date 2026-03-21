from __future__ import annotations

from codex_discord_bot.runtime.startup import ApplicationContext


async def shutdown_application_context(app: ApplicationContext) -> None:
    await app.close()
