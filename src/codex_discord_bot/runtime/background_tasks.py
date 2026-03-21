from __future__ import annotations

import asyncio

from codex_discord_bot.logging import get_logger
from codex_discord_bot.runtime.startup import ApplicationContext

logger = get_logger(__name__)


async def run_idle_worker_reaper(app: ApplicationContext) -> None:
    while True:
        await asyncio.sleep(60)
        if app.worker_pool is None:
            continue
        reaper = getattr(app.worker_pool, "reap_idle_workers", None)
        if reaper is None:
            continue
        closed = await reaper()
        if closed:
            logger.info("worker.reaped", count=closed)
