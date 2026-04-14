from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from codex_discord_bot.codex.stream_events import TurnStartedEvent
from codex_discord_bot.discord.handlers import thread_messages
from codex_discord_bot.persistence.enums import SessionStatus


class FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.id = int(thread_id)
        self.parent_id = 1
        self.guild = SimpleNamespace(id=123456)

    async def send(self, *_args, **_kwargs) -> None:
        raise AssertionError("未初始化消息不应创建控制消息")


class FakeMessage:
    def __init__(self, channel: FakeThread, *, content: str) -> None:
        self.author = SimpleNamespace(bot=False, id=654321)
        self.channel = channel
        self.guild = channel.guild
        self.content = content
        self.id = 777
        self.attachments: list[object] = []
        self.replies: list[str] = []

    async def reply(self, content: str, *, mention_author: bool) -> None:
        assert mention_author is False
        self.replies.append(content)


class FakeAuditService:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **payload) -> None:
        self.records.append(payload)


def test_uninitialized_thread_message_does_not_invoke_codex() -> None:
    async def fail_collect(*_args, **_kwargs):
        raise AssertionError("未初始化消息不应进入附件处理")

    def fail_build(*_args, **_kwargs):
        raise AssertionError("未初始化消息不应构造 Codex 输入")

    async def scenario() -> None:
        audit_service = FakeAuditService()
        route = SimpleNamespace(
            workspace=SimpleNamespace(id=6),
            session=SimpleNamespace(
                codex_thread_id=None,
                status=SessionStatus.uninitialized,
            ),
        )
        bot = SimpleNamespace(
            app_state=SimpleNamespace(
                session_router=SimpleNamespace(
                    ensure_route_for_thread=_async_return(route),
                ),
                audit_service=audit_service,
            )
        )
        message = FakeMessage(FakeThread("1001"), content="你好")

        with patch.object(thread_messages.discord, "Thread", FakeThread):
            with patch.object(thread_messages, "collect_supported_attachments", fail_collect):
                with patch.object(thread_messages, "build_message_input_items", fail_build):
                    await thread_messages.handle_thread_message(bot, message)

        assert message.replies == [
            "当前线程尚未初始化 Codex 会话，请先执行 `/codex session new` 创建新会话，"
            "或执行 `/codex session list` 后再用 `/codex session resume` 恢复历史会话。"
            "当前消息不会发送给 Codex。"
        ]
        assert audit_service.records == [
            {
                "action": "thread_message_blocked_uninitialized",
                "guild_id": "123456",
                "discord_thread_id": "1001",
                "actor_id": "654321",
                "payload": {
                    "message_id": "777",
                    "content_length": 2,
                    "attachment_count": 0,
                },
            }
        ]

    asyncio.run(scenario())


def test_initialized_thread_recovers_when_bound_thread_is_missing() -> None:
    class RunningThread(FakeThread):
        def __init__(self, thread_id: str) -> None:
            super().__init__(thread_id)
            self.sent_messages: list[dict[str, object]] = []

        async def send(self, content: str, **kwargs) -> SimpleNamespace:
            self.sent_messages.append({"content": content, **kwargs})
            return SimpleNamespace(id=888, content=content)

    class FakeWorker:
        def __init__(self) -> None:
            self.run_calls = 0

        async def run_streamed_turn(
            self,
            session,
            workspace,
            input_items,
            *,
            on_event,
            on_approval_request,
        ):
            del workspace, input_items, on_approval_request
            self.run_calls += 1
            if self.run_calls == 1:
                assert session.codex_thread_id == "thr_stale"
                raise RuntimeError("no rollout found for thread id thr_stale")

            assert session.codex_thread_id is None
            await on_event(TurnStartedEvent(thread_id="thr_new", turn_id="turn_1"))
            return SimpleNamespace(
                thread_id="thr_new",
                turn_id="turn_1",
                final_text="已完成",
                turn_status="completed",
                assistant_messages=[],
                image_artifacts=[],
            )

        async def read_thread(self, thread_id: str, *, include_turns: bool) -> dict[str, object]:
            assert thread_id == "thr_new"
            assert include_turns is False
            return {
                "id": "thr_new",
                "cwd": "/repo",
                "preview": "恢复成功",
                "source": {"custom": "discord-bot"},
            }

    class FakeLease:
        def __init__(self, worker: FakeWorker) -> None:
            self.worker = worker

        async def __aenter__(self) -> FakeWorker:
            return self.worker

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class FakeWorkerPool:
        def __init__(self, worker: FakeWorker) -> None:
            self.worker = worker
            self.lease_keys: list[str] = []

        def is_busy(self, _thread_id: str) -> bool:
            return False

        def get_worker(self, _thread_id: str) -> None:
            return None

        def lease(self, key: str) -> FakeLease:
            self.lease_keys.append(key)
            return FakeLease(self.worker)

    class FakeSessionService:
        def __init__(self) -> None:
            self.bind_calls: list[dict[str, str | None]] = []
            self.detach_calls: list[str] = []
            self.running_calls: list[dict[str, str | None]] = []
            self.ready_calls: list[dict[str, str | None]] = []
            self.error_calls: list[dict[str, str | None]] = []

        async def bind_codex_thread(self, *, discord_thread_id: str, codex_thread_id: str | None) -> None:
            self.bind_calls.append(
                {
                    "discord_thread_id": discord_thread_id,
                    "codex_thread_id": codex_thread_id,
                }
            )

        async def detach_codex_thread(self, *, discord_thread_id: str) -> None:
            self.detach_calls.append(discord_thread_id)

        async def mark_running(
            self,
            *,
            discord_thread_id: str,
            active_turn_id: str | None = None,
            last_bot_message_id: str | None = None,
        ) -> None:
            self.running_calls.append(
                {
                    "discord_thread_id": discord_thread_id,
                    "active_turn_id": active_turn_id,
                    "last_bot_message_id": last_bot_message_id,
                }
            )

        async def mark_ready(
            self,
            *,
            discord_thread_id: str,
            last_bot_message_id: str | None = None,
        ) -> None:
            self.ready_calls.append(
                {
                    "discord_thread_id": discord_thread_id,
                    "last_bot_message_id": last_bot_message_id,
                }
            )

        async def mark_error(
            self,
            *,
            discord_thread_id: str,
            last_bot_message_id: str | None = None,
        ) -> None:
            self.error_calls.append(
                {
                    "discord_thread_id": discord_thread_id,
                    "last_bot_message_id": last_bot_message_id,
                }
            )

    class FakeCodexThreadService:
        def __init__(self) -> None:
            self.release_calls: list[dict[str, str]] = []
            self.ensure_calls: list[dict[str, str]] = []
            self.sync_calls: list[dict[str, object]] = []

        async def get_by_codex_thread_id(self, _codex_thread_id: str) -> None:
            return None

        async def release_binding_if_owned(
            self,
            *,
            codex_thread_id: str,
            discord_thread_id: str,
        ) -> None:
            self.release_calls.append(
                {
                    "codex_thread_id": codex_thread_id,
                    "discord_thread_id": discord_thread_id,
                }
            )

        async def ensure_thread_available_for_discord(
            self,
            *,
            workspace_id: int,
            codex_thread_id: str,
            discord_thread_id: str,
        ) -> None:
            self.ensure_calls.append(
                {
                    "workspace_id": str(workspace_id),
                    "codex_thread_id": codex_thread_id,
                    "discord_thread_id": discord_thread_id,
                }
            )

        async def sync_thread_from_payload(
            self,
            *,
            workspace_id: int,
            thread_payload: dict[str, object],
            archived: bool,
            source_override: object | None = None,
        ) -> None:
            self.sync_calls.append(
                {
                    "workspace_id": workspace_id,
                    "thread_payload": thread_payload,
                    "archived": archived,
                    "source_override": source_override,
                }
            )

    class FakeTurnOutputController:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def bind_turn(self, *, codex_thread_id: str, turn_id: str) -> None:
            assert codex_thread_id == "thr_new"
            assert turn_id == "turn_1"

        async def handle_event(self, _event) -> None:
            return None

        async def finalize(self, _result) -> SimpleNamespace:
            return SimpleNamespace(
                state=SimpleNamespace(value="completed"),
                last_message_id="888",
            )

        async def fail(self, _error: str) -> SimpleNamespace:
            return SimpleNamespace(last_message_id="888")

    async def scenario() -> None:
        worker = FakeWorker()
        worker_pool = FakeWorkerPool(worker)
        audit_service = FakeAuditService()
        session_service = FakeSessionService()
        codex_thread_service = FakeCodexThreadService()
        route = SimpleNamespace(
            workspace=SimpleNamespace(id=6, cwd="/repo"),
            session=SimpleNamespace(
                codex_thread_id="thr_stale",
                status=SessionStatus.ready,
                active_turn_id=None,
            ),
        )
        bot = SimpleNamespace(
            app_state=SimpleNamespace(
                session_router=SimpleNamespace(
                    ensure_route_for_thread=_async_return(route),
                ),
                audit_service=audit_service,
                worker_pool=worker_pool,
                codex_thread_service=codex_thread_service,
                session_service=session_service,
                artifact_service=SimpleNamespace(artifact_root="/tmp"),
                settings=SimpleNamespace(),
                turn_output_service=SimpleNamespace(),
                approval_service=SimpleNamespace(),
            )
        )
        message = FakeMessage(RunningThread("1002"), content="你好")

        with patch.object(thread_messages.discord, "Thread", FakeThread):
            with patch.object(thread_messages, "collect_supported_attachments", _async_return([])):
                with patch.object(
                    thread_messages,
                    "build_message_input_items",
                    lambda **_kwargs: [{"type": "text", "text": "你好"}],
                ):
                    with patch.object(thread_messages, "TurnOutputController", FakeTurnOutputController):
                        with patch.object(
                            thread_messages,
                            "SessionControlView",
                            lambda _app_state: object(),
                        ):
                            await thread_messages.handle_thread_message(bot, message)

        assert worker_pool.lease_keys == ["1002"]
        assert worker.run_calls == 2
        assert session_service.detach_calls == ["1002"]
        assert session_service.bind_calls[-1] == {
            "discord_thread_id": "1002",
            "codex_thread_id": "thr_new",
        }
        assert session_service.ready_calls[-1] == {
            "discord_thread_id": "1002",
            "last_bot_message_id": "888",
        }
        assert session_service.error_calls == []
        assert codex_thread_service.release_calls == [
            {
                "codex_thread_id": "thr_stale",
                "discord_thread_id": "1002",
            }
        ]
        assert codex_thread_service.ensure_calls == [
            {
                "workspace_id": "6",
                "codex_thread_id": "thr_new",
                "discord_thread_id": "1002",
            }
        ]
        assert codex_thread_service.sync_calls[0]["thread_payload"]["id"] == "thr_new"
        assert any(
            record["action"] == "thread_message_recovered_missing_thread"
            for record in audit_service.records
        )
        assert message.replies == []

    asyncio.run(scenario())


def _async_return(value):
    async def inner(*args, **kwargs):
        del args, kwargs
        return value

    return inner
