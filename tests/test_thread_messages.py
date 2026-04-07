from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

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


def _async_return(value):
    async def inner(*args, **kwargs):
        del args, kwargs
        return value

    return inner
