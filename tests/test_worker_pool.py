from __future__ import annotations

import asyncio

from codex_discord_bot.codex.worker_pool import WorkerPool
from codex_discord_bot.config import Settings


def test_worker_pool_reaps_idle_worker() -> None:
    async def scenario() -> None:
        settings = Settings(
            discord_bot_token="token",
            worker_idle_timeout_seconds=0,
        )
        pool = WorkerPool(settings)

        async with pool.lease("thread-1") as _worker:
            assert pool.has_worker("thread-1") is True

        closed = await pool.reap_idle_workers()
        assert closed == 1
        assert pool.has_worker("thread-1") is False

    asyncio.run(scenario())
