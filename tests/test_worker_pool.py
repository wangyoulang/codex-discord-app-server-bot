from __future__ import annotations

import asyncio

from codex_discord_bot.codex.app_server_client import Notification
from codex_discord_bot.codex.worker import CodexWorker
from codex_discord_bot.codex.worker_pool import WorkerPool
from codex_discord_bot.config import Settings
from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace


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


def test_worker_uses_only_cwd_overrides_for_local_codex_config() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.thread_start_params: dict | None = None
            self.turn_start_params: dict | None = None

        def thread_start(self, params: dict) -> dict:
            self.thread_start_params = params
            return {"thread": {"id": "thr_1"}}

        def turn_start(self, thread_id: str, text: str, *, params: dict | None = None) -> dict:
            assert thread_id == "thr_1"
            assert text == "你好"
            self.turn_start_params = params
            return {"turn": {"id": "turn_1"}}

        def next_notification(self) -> Notification:
            return Notification(method="turn/completed", payload={"turn": {"id": "turn_1"}})

        def thread_read(self, thread_id: str, *, include_turns: bool) -> dict:
            assert thread_id == "thr_1"
            assert include_turns is True
            return {
                "thread": {
                    "turns": [
                        {
                            "id": "turn_1",
                            "items": [{"type": "agentMessage", "text": "已完成"}],
                        }
                    ]
                }
            }

    async def noop_delta(_delta: str) -> None:
        return None

    async def noop_turn_started(_thread_id: str, _turn_id: str) -> None:
        return None

    async def noop_approval(_envelope) -> dict:
        return {"decision": "decline"}

    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        worker = CodexWorker(settings, worker_key="thread-1")
        fake_client = FakeClient()
        worker._client = fake_client  # type: ignore[assignment]
        session = DiscordSession(
            discord_thread_id="discord_thread_1",
            workspace_id=1,
            status=SessionStatus.ready,
        )
        workspace = Workspace(
            guild_id="guild_1",
            forum_channel_id="forum_1",
            name="demo",
            cwd="/repo",
        )

        thread_id, turn_id, final_text = await worker.run_streamed_text_turn(
            session,
            workspace,
            "你好",
            on_delta=noop_delta,
            on_turn_started=noop_turn_started,
            on_approval_request=noop_approval,
        )

        assert thread_id == "thr_1"
        assert turn_id == "turn_1"
        assert final_text == "已完成"
        assert fake_client.thread_start_params == {"cwd": "/repo"}
        assert fake_client.turn_start_params == {"cwd": "/repo"}

    asyncio.run(scenario())


def test_worker_resume_uses_only_cwd_override() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.thread_resume_call: tuple[str, dict] | None = None

        def thread_resume(self, thread_id: str, params: dict) -> dict:
            self.thread_resume_call = (thread_id, params)
            return {"thread": {"id": thread_id}}

    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        worker = CodexWorker(settings, worker_key="thread-1")
        fake_client = FakeClient()
        worker._client = fake_client  # type: ignore[assignment]
        session = DiscordSession(
            discord_thread_id="discord_thread_1",
            workspace_id=1,
            codex_thread_id="thr_existing",
            status=SessionStatus.ready,
        )
        workspace = Workspace(
            guild_id="guild_1",
            forum_channel_id="forum_1",
            name="demo",
            cwd="/repo",
        )

        thread_id = await worker.ensure_thread(session, workspace)

        assert thread_id == "thr_existing"
        assert fake_client.thread_resume_call == ("thr_existing", {"cwd": "/repo"})

    asyncio.run(scenario())
