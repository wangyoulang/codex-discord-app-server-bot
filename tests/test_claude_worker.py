from __future__ import annotations

import asyncio
import sys
import types

from codex_discord_bot.claude.worker import ClaudeWorker
from codex_discord_bot.config import Settings
from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.providers.events import AgentMessageDeltaEvent
from codex_discord_bot.providers.events import ItemCompletedEvent
from codex_discord_bot.providers.events import ItemStartedEvent
from codex_discord_bot.providers.events import TurnCompletedEvent
from codex_discord_bot.providers.events import TurnStartedEvent


def test_claude_worker_stream_events_do_not_deadlock_current_event_loop(monkeypatch) -> None:
    sdk_module = types.ModuleType("claude_agent_sdk")

    class FakeStreamEvent:
        def __init__(self, *, session_id: str, event: dict) -> None:
            self.session_id = session_id
            self.event = event

    class FakeResultMessage:
        def __init__(
            self,
            *,
            session_id: str,
            is_error: bool = False,
            result: str | None = None,
            subtype: str = "success",
        ) -> None:
            self.session_id = session_id
            self.is_error = is_error
            self.result = result
            self.subtype = subtype

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content: list[object] = []
            self.error: str | None = None

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeToolUseBlock:
        def __init__(self, name: str, payload: dict) -> None:
            self.name = name
            self.input = payload

    class FakePermissionResultAllow:
        pass

    class FakePermissionResultDeny:
        def __init__(self, *, message: str) -> None:
            self.message = message

    class FakeClaudeSDKClient:
        def __init__(self, *, options) -> None:
            self.options = options
            self.connected = False
            self.disconnected = False
            self.query_payloads: list[dict] = []
            self.interrupted = False

        async def connect(self) -> None:
            self.connected = True

        async def query(self, messages, *, session_id: str) -> None:
            assert session_id == "default"
            async for message in messages:
                self.query_payloads.append(message)

        async def receive_messages(self):
            yield FakeStreamEvent(
                session_id="claude-session-1",
                event={
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text"},
                },
            )
            yield FakeStreamEvent(
                session_id="claude-session-1",
                event={
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"text": "pong"},
                },
            )
            yield FakeStreamEvent(
                session_id="claude-session-1",
                event={"type": "content_block_stop", "index": 0},
            )
            yield FakeResultMessage(session_id="claude-session-1", result="pong")

        async def disconnect(self) -> None:
            self.disconnected = True

        async def interrupt(self) -> None:
            self.interrupted = True

    sdk_module.StreamEvent = FakeStreamEvent
    sdk_module.ResultMessage = FakeResultMessage
    sdk_module.AssistantMessage = FakeAssistantMessage
    sdk_module.TextBlock = FakeTextBlock
    sdk_module.ToolUseBlock = FakeToolUseBlock
    sdk_module.ClaudeSDKClient = FakeClaudeSDKClient
    sdk_module.PermissionResultAllow = FakePermissionResultAllow
    sdk_module.PermissionResultDeny = FakePermissionResultDeny
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk_module)
    monkeypatch.setattr(
        "codex_discord_bot.claude.worker.build_claude_options",
        lambda settings, *, cwd, resume_session_id, can_use_tool: {
            "cwd": cwd,
            "resume_session_id": resume_session_id,
            "can_use_tool": can_use_tool,
        },
    )

    async def scenario() -> None:
        settings = Settings(discord_bot_token="token")
        worker = ClaudeWorker(settings, worker_key="claude:thread-1")
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
            await asyncio.sleep(0)
            events.append(event)

        async def on_approval_request(_envelope) -> dict:
            return {"decision": "decline"}

        result = await asyncio.wait_for(
            worker.run_streamed_turn(
                session,
                workspace,
                "请只回复 pong",
                on_event=on_event,
                on_approval_request=on_approval_request,
            ),
            timeout=1,
        )

        assert result.thread_id == "claude-session-1"
        assert result.final_text == "pong"
        assert result.turn_status == "completed"
        assert [type(event) for event in events] == [
            TurnStartedEvent,
            ItemStartedEvent,
            AgentMessageDeltaEvent,
            ItemCompletedEvent,
            TurnCompletedEvent,
        ]
        assert events[0].thread_id == "claude-session-1"
        assert events[1].item_type == "agentMessage"
        assert events[2].delta == "pong"
        assert events[3].item["text"] == "pong"
        assert events[4].status == "completed"

    asyncio.run(scenario())


def test_claude_worker_auto_allow_disables_discord_approval_callback(monkeypatch) -> None:
    sdk_module = types.ModuleType("claude_agent_sdk")

    class FakeStreamEvent:
        def __init__(self, *, session_id: str, event: dict) -> None:
            self.session_id = session_id
            self.event = event

    class FakeResultMessage:
        def __init__(self, *, session_id: str) -> None:
            self.session_id = session_id
            self.is_error = False
            self.result = "pong"
            self.subtype = "success"

    class FakeAssistantMessage:
        def __init__(self) -> None:
            self.content: list[object] = []
            self.error: str | None = None

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeToolUseBlock:
        def __init__(self, name: str, payload: dict) -> None:
            self.name = name
            self.input = payload

    class FakeClaudeSDKClient:
        def __init__(self, *, options) -> None:
            self.options = options

        async def connect(self) -> None:
            return None

        async def query(self, messages, *, session_id: str) -> None:
            async for _message in messages:
                pass

        async def receive_messages(self):
            yield FakeStreamEvent(
                session_id="claude-session-2",
                event={
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text"},
                },
            )
            yield FakeStreamEvent(
                session_id="claude-session-2",
                event={
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"text": "pong"},
                },
            )
            yield FakeStreamEvent(
                session_id="claude-session-2",
                event={"type": "content_block_stop", "index": 0},
            )
            yield FakeResultMessage(session_id="claude-session-2")

        async def disconnect(self) -> None:
            return None

        async def interrupt(self) -> None:
            return None

    sdk_module.StreamEvent = FakeStreamEvent
    sdk_module.ResultMessage = FakeResultMessage
    sdk_module.AssistantMessage = FakeAssistantMessage
    sdk_module.TextBlock = FakeTextBlock
    sdk_module.ToolUseBlock = FakeToolUseBlock
    sdk_module.ClaudeSDKClient = FakeClaudeSDKClient
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk_module)

    captured_options: dict[str, object] = {}

    def fake_build_options(settings, *, cwd, resume_session_id, can_use_tool):
        captured_options["cwd"] = cwd
        captured_options["resume_session_id"] = resume_session_id
        captured_options["can_use_tool"] = can_use_tool
        return {}

    monkeypatch.setattr(
        "codex_discord_bot.claude.worker.build_claude_options",
        fake_build_options,
    )

    async def scenario() -> None:
        settings = Settings(
            discord_bot_token="token",
            claude_approval_policy="auto_allow",
            claude_settings_mode="managed",
        )
        worker = ClaudeWorker(settings, worker_key="claude:auto-allow")
        session = DiscordSession(
            discord_thread_id="discord_thread_2",
            workspace_id=1,
            status=SessionStatus.ready,
        )
        workspace = Workspace(
            guild_id="guild_1",
            forum_channel_id="forum_1",
            name="demo",
            cwd="/repo",
        )

        async def on_event(_event) -> None:
            return None

        async def on_approval_request(_envelope) -> dict:
            raise AssertionError("auto_allow 模式下不应该走 Discord 审批回调")

        result = await worker.run_streamed_turn(
            session,
            workspace,
            "请只回复 pong",
            on_event=on_event,
            on_approval_request=on_approval_request,
        )

        assert result.final_text == "pong"
        assert captured_options["can_use_tool"] is None

    asyncio.run(scenario())
