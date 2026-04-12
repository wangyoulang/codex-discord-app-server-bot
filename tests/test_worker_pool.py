from __future__ import annotations

import asyncio

from codex_discord_bot.codex.app_server_client import Notification
from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_events import TurnCompletedEvent
from codex_discord_bot.codex.stream_events import TurnStartedEvent
from codex_discord_bot.codex.stream_renderer import OutputImageArtifact
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


def test_worker_pool_force_reset_closes_worker_entry() -> None:
    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        pool = WorkerPool(settings)

        async with pool.lease("thread-1") as _worker:
            assert pool.has_worker("thread-1") is True

        await pool.force_reset("thread-1")
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


def test_worker_supports_generic_input_items_for_steer() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.steer_calls: list[tuple[str, list[dict[str, str]], str]] = []

        def turn_steer(
            self,
            thread_id: str,
            input_items: list[dict[str, str]],
            *,
            expected_turn_id: str,
        ) -> dict:
            self.steer_calls.append((thread_id, input_items, expected_turn_id))
            return {"turnId": expected_turn_id}

    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        worker = CodexWorker(settings, worker_key="thread-1")
        fake_client = FakeClient()
        worker._client = fake_client  # type: ignore[assignment]
        worker._set_active_turn("thr_1", "turn_1")

        input_items = [
            {"type": "text", "text": "继续分析"},
            {"type": "localImage", "path": "/tmp/example.webp"},
        ]
        steered_turn_id = await worker.steer_turn(input_items)

        assert steered_turn_id == "turn_1"
        assert fake_client.steer_calls == [("thr_1", input_items, "turn_1")]

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

    async def noop_event(_event) -> None:
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

        result = await worker.run_streamed_text_turn(
            session,
            workspace,
            "你好",
            on_event=noop_event,
            on_approval_request=noop_approval,
        )

        assert result.thread_id == "thr_1"
        assert result.turn_id == "turn_1"
        assert result.final_text == "已完成"
        assert result.turn_status == "completed"
        assert fake_client.thread_start_params == {"cwd": "/repo"}
        assert fake_client.turn_start_params == {"cwd": "/repo"}

    asyncio.run(scenario())


def test_worker_run_streamed_turn_supports_generic_input_items() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.thread_start_params: dict | None = None
            self.turn_start_input: list[dict[str, str]] | None = None
            self.turn_start_params: dict | None = None

        def thread_start(self, params: dict) -> dict:
            self.thread_start_params = params
            return {"thread": {"id": "thr_1"}}

        def turn_start(
            self,
            thread_id: str,
            input_items: list[dict[str, str]],
            *,
            params: dict | None = None,
        ) -> dict:
            assert thread_id == "thr_1"
            self.turn_start_input = input_items
            self.turn_start_params = params
            return {"turn": {"id": "turn_1"}}

        def next_notification(self) -> Notification:
            return Notification(
                method="turn/completed",
                payload={"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
            )

        def thread_read(self, thread_id: str, *, include_turns: bool) -> dict:
            assert thread_id == "thr_1"
            assert include_turns is True
            return {
                "thread": {
                    "turns": [
                        {
                            "id": "turn_1",
                            "items": [{"type": "agentMessage", "text": "已收到图片"}],
                        }
                    ]
                }
            }

    async def noop_event(_event) -> None:
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
        input_items = [
            {"type": "text", "text": "请帮我看图"},
            {"type": "localImage", "path": "/tmp/example.webp"},
        ]

        result = await worker.run_streamed_turn(
            session,
            workspace,
            input_items,
            on_event=noop_event,
            on_approval_request=noop_approval,
        )

        assert result.thread_id == "thr_1"
        assert result.turn_id == "turn_1"
        assert result.final_text == "已收到图片"
        assert result.turn_status == "completed"
        assert fake_client.thread_start_params == {"cwd": "/repo"}
        assert fake_client.turn_start_params == {"cwd": "/repo"}
        assert fake_client.turn_start_input == input_items

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


def test_worker_start_new_thread_uses_only_cwd_override() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.thread_start_params: dict | None = None

        def thread_start(self, params: dict) -> dict:
            self.thread_start_params = params
            return {"thread": {"id": "thr_new"}}

    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        worker = CodexWorker(settings, worker_key="thread-1")
        fake_client = FakeClient()
        worker._client = fake_client  # type: ignore[assignment]
        workspace = Workspace(
            guild_id="guild_1",
            forum_channel_id="forum_1",
            name="demo",
            cwd="/repo",
        )

        thread_id = await worker.start_new_thread(workspace)

        assert thread_id == "thr_new"
        assert fake_client.thread_start_params == {"cwd": "/repo"}

    asyncio.run(scenario())


def test_worker_emits_structured_stream_events() -> None:
    class FakeClient:
        def thread_start(self, params: dict) -> dict:
            assert params == {"cwd": "/repo"}
            return {"thread": {"id": "thr_1"}}

        def turn_start(self, thread_id: str, text: str, *, params: dict | None = None) -> dict:
            assert thread_id == "thr_1"
            assert text == "你好"
            assert params == {"cwd": "/repo"}
            return {"turn": {"id": "turn_1"}}

        def next_notification(self) -> Notification:
            if not self.notifications:
                raise AssertionError("缺少通知")
            return self.notifications.pop(0)

        def thread_read(self, thread_id: str, *, include_turns: bool) -> dict:
            assert thread_id == "thr_1"
            assert include_turns is True
            return {
                "thread": {
                    "turns": [
                        {
                            "id": "turn_1",
                            "items": [{"type": "agentMessage", "id": "item_1", "text": "你好，世界"}],
                        }
                    ]
                }
            }

        def __init__(self) -> None:
            self.notifications = [
                Notification(method="turn/started", payload={"threadId": "thr_1", "turn": {"id": "turn_1"}}),
                Notification(
                    method="item/started",
                    payload={
                        "threadId": "thr_1",
                        "turnId": "turn_1",
                        "item": {"id": "item_1", "type": "agentMessage", "text": ""},
                    },
                ),
                Notification(
                    method="item/agentMessage/delta",
                    payload={
                        "threadId": "thr_1",
                        "turnId": "turn_1",
                        "itemId": "item_1",
                        "delta": "你好，",
                    },
                ),
                Notification(
                    method="item/agentMessage/delta",
                    payload={
                        "threadId": "thr_1",
                        "turnId": "turn_1",
                        "itemId": "item_1",
                        "delta": "世界",
                    },
                ),
                Notification(
                    method="item/completed",
                    payload={
                        "threadId": "thr_1",
                        "turnId": "turn_1",
                        "item": {"id": "item_1", "type": "agentMessage", "text": "你好，世界"},
                    },
                ),
                Notification(
                    method="turn/completed",
                    payload={"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
                ),
            ]

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

        events = []

        async def on_event(event) -> None:
            events.append(event)

        result = await worker.run_streamed_text_turn(
            session,
            workspace,
            "你好",
            on_event=on_event,
            on_approval_request=noop_approval,
        )

        assert result.final_text == "你好，世界"
        assert [type(event) for event in events] == [
            TurnStartedEvent,
            ItemStartedEvent,
            AgentMessageDeltaEvent,
            AgentMessageDeltaEvent,
            ItemCompletedEvent,
            TurnCompletedEvent,
        ]
        assert events[0].turn_id == "turn_1"
        assert events[1].item_id == "item_1"
        assert events[2].delta == "你好，"
        assert events[4].item["text"] == "你好，世界"
        assert events[5].status == "completed"

    asyncio.run(scenario())


def test_worker_collects_image_artifacts_without_placeholder_text() -> None:
    class FakeClient:
        def thread_start(self, params: dict) -> dict:
            assert params == {"cwd": "/repo"}
            return {"thread": {"id": "thr_1"}}

        def turn_start(self, thread_id: str, text: str, *, params: dict | None = None) -> dict:
            assert thread_id == "thr_1"
            assert text == "生成截图"
            assert params == {"cwd": "/repo"}
            return {"turn": {"id": "turn_1"}}

        def next_notification(self) -> Notification:
            return Notification(
                method="turn/completed",
                payload={"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
            )

        def thread_read(self, thread_id: str, *, include_turns: bool) -> dict:
            assert thread_id == "thr_1"
            assert include_turns is True
            return {
                "thread": {
                    "turns": [
                        {
                            "id": "turn_1",
                            "items": [
                                {
                                    "id": "img_1",
                                    "type": "imageView",
                                    "path": "/tmp/example.png",
                                }
                            ],
                        }
                    ]
                }
            }

    async def noop_event(_event) -> None:
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

        result = await worker.run_streamed_text_turn(
            session,
            workspace,
            "生成截图",
            on_event=noop_event,
            on_approval_request=noop_approval,
        )

        assert result.final_text == ""
        assert result.image_artifacts == [
            OutputImageArtifact(
                item_id="img_1",
                path="/tmp/example.png",
                source_type="imageView",
            )
        ]

    asyncio.run(scenario())



def test_worker_collects_media_directive_artifacts_and_strips_directive_text() -> None:
    class FakeClient:
        def thread_start(self, params: dict) -> dict:
            assert params == {"cwd": "/repo"}
            return {"thread": {"id": "thr_1"}}

        def turn_start(self, thread_id: str, text: str, *, params: dict | None = None) -> dict:
            assert thread_id == "thr_1"
            assert text == "展示截图"
            assert params == {"cwd": "/repo"}
            return {"turn": {"id": "turn_1"}}

        def next_notification(self) -> Notification:
            return Notification(
                method="turn/completed",
                payload={"threadId": "thr_1", "turn": {"id": "turn_1", "status": "completed"}},
            )

        def thread_read(self, thread_id: str, *, include_turns: bool) -> dict:
            assert thread_id == "thr_1"
            assert include_turns is True
            return {
                "thread": {
                    "turns": [
                        {
                            "id": "turn_1",
                            "items": [
                                {
                                    "id": "item_1",
                                    "type": "agentMessage",
                                    "text": "截图如下\nMEDIA: ./artifacts/screen.png\n请确认",
                                }
                            ],
                        }
                    ]
                }
            }

    async def noop_event(_event) -> None:
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

        result = await worker.run_streamed_text_turn(
            session,
            workspace,
            "展示截图",
            on_event=noop_event,
            on_approval_request=noop_approval,
        )

        assert result.final_text == "截图如下\n请确认"
        assert len(result.assistant_messages) == 1
        assert result.assistant_messages[0].item_id == "item_1"
        assert result.assistant_messages[0].text == "截图如下\n请确认"
        assert result.image_artifacts == [
            OutputImageArtifact(
                item_id="item_1:media:0",
                path="/repo/artifacts/screen.png",
                source_type="mediaDirective",
                parent_item_id="item_1",
            )
        ]

    asyncio.run(scenario())
