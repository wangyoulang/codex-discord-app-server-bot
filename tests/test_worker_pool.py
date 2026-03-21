from __future__ import annotations

import asyncio

from codex_discord_bot.codex.worker import CodexWorker
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


def test_worker_supports_steer_and_interrupt_on_active_turn() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.steer_calls: list[tuple[str, str, str]] = []
            self.interrupt_calls: list[tuple[str, str]] = []

        def turn_steer(self, thread_id: str, text: str, *, expected_turn_id: str) -> dict:
            self.steer_calls.append((thread_id, text, expected_turn_id))
            return {"turnId": expected_turn_id}

        def turn_interrupt(self, thread_id: str, turn_id: str) -> dict:
            self.interrupt_calls.append((thread_id, turn_id))
            return {}

    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        worker = CodexWorker(settings, worker_key="thread-1")
        fake_client = FakeClient()
        worker._client = fake_client  # type: ignore[assignment]
        worker._set_active_turn("thr_1", "turn_1")

        steered_turn_id = await worker.steer_text_turn("继续往下做")
        interrupted_turn_id = await worker.interrupt_active_turn()

        assert steered_turn_id == "turn_1"
        assert interrupted_turn_id == "turn_1"
        assert fake_client.steer_calls == [("thr_1", "继续往下做", "turn_1")]
        assert fake_client.interrupt_calls == [("thr_1", "turn_1")]

    asyncio.run(scenario())
