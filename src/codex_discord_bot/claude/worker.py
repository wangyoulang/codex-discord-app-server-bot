from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import threading
from typing import TYPE_CHECKING
from typing import Any
from collections.abc import AsyncIterator
from uuid import uuid4

from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.config import Settings
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace
from codex_discord_bot.providers.events import AgentMessageDeltaEvent
from codex_discord_bot.providers.events import ItemCompletedEvent
from codex_discord_bot.providers.events import ItemStartedEvent
from codex_discord_bot.providers.events import TurnCompletedEvent
from codex_discord_bot.providers.events import TurnStartedEvent

from .client_factory import build_claude_options

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient


@dataclass(slots=True)
class ExecutionCallbacks:
    loop: asyncio.AbstractEventLoop
    on_event: object
    on_approval_request: object


@dataclass(slots=True)
class ActiveTurn:
    thread_id: str
    turn_id: str


@dataclass(slots=True)
class TurnRunResult:
    thread_id: str
    turn_id: str
    final_text: str
    turn_status: str
    error_message: str | None = None
    assistant_messages: list[AssistantMessageSnapshot] = field(default_factory=list)


UserInputItems = list[dict[str, Any]] | dict[str, Any] | str


class ClaudeWorker:
    def __init__(self, settings: Settings, *, worker_key: str) -> None:
        self.settings = settings
        self.worker_key = worker_key
        self._state_lock = threading.Lock()
        self._active_turn: ActiveTurn | None = None
        self._active_client: ClaudeSDKClient | None = None
        self._known_session_ids: set[str] = set()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        client = self._active_client
        self._active_client = None
        if client is not None:
            await client.disconnect()

    def get_active_turn(self) -> ActiveTurn | None:
        with self._state_lock:
            if self._active_turn is None:
                return None
            return ActiveTurn(
                thread_id=self._active_turn.thread_id,
                turn_id=self._active_turn.turn_id,
            )

    def _set_active_turn(self, thread_id: str, turn_id: str) -> None:
        with self._state_lock:
            self._active_turn = ActiveTurn(thread_id=thread_id, turn_id=turn_id)

    def _clear_active_turn(self, thread_id: str, turn_id: str) -> None:
        with self._state_lock:
            if self._active_turn is None:
                return
            if self._active_turn.thread_id != thread_id or self._active_turn.turn_id != turn_id:
                return
            self._active_turn = None

    async def ensure_thread(
        self,
        session: DiscordSession,
        workspace: Workspace,
    ) -> str:
        if session.codex_thread_id:
            return session.codex_thread_id
        return f"pending:{workspace.id}:{session.discord_thread_id}"

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int = 10,
        search_term: str | None = None,
        archived: bool = False,
    ) -> list[dict[str, Any]]:
        del archived
        try:
            from claude_agent_sdk import list_sessions
        except ImportError as exc:
            raise RuntimeError(
                "当前环境缺少 claude-agent-sdk，请先执行 `uv sync` 安装依赖。"
            ) from exc

        infos = list_sessions(directory=cwd, limit=limit, include_worktrees=False)
        payloads = [self._session_info_to_payload(info, fallback_cwd=cwd) for info in infos]
        if not search_term:
            return payloads
        keyword = search_term.lower()
        return [
            payload
            for payload in payloads
            if keyword in payload["id"].lower()
            or keyword in str(payload.get("preview") or "").lower()
            or keyword in str(payload.get("cwd") or "").lower()
        ]

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool,
    ) -> dict[str, Any]:
        try:
            from claude_agent_sdk import get_session_info
            from claude_agent_sdk import get_session_messages
        except ImportError as exc:
            raise RuntimeError(
                "当前环境缺少 claude-agent-sdk，请先执行 `uv sync` 安装依赖。"
            ) from exc

        info = get_session_info(thread_id)
        if info is None:
            raise RuntimeError(f"Claude 会话不存在：{thread_id}")
        payload = self._session_info_to_payload(info, fallback_cwd=info.cwd or "")
        if include_turns:
            payload["turns"] = [
                {
                    "id": f"claude-turn:{thread_id}",
                    "items": [
                        message.message
                        for message in get_session_messages(thread_id, directory=info.cwd)
                    ],
                }
            ]
        return payload

    async def archive_thread(self, thread_id: str) -> None:
        self._known_session_ids.add(thread_id)

    async def unarchive_thread(self, thread_id: str) -> dict[str, Any]:
        return await self.read_thread(thread_id, include_turns=False)

    async def run_streamed_text_turn(
        self,
        session: DiscordSession,
        workspace: Workspace,
        text: str,
        *,
        on_event,
        on_approval_request,
    ) -> TurnRunResult:
        return await self.run_streamed_turn(
            session,
            workspace,
            text,
            on_event=on_event,
            on_approval_request=on_approval_request,
        )

    async def run_streamed_turn(
        self,
        session: DiscordSession,
        workspace: Workspace,
        input_items: UserInputItems,
        *,
        on_event,
        on_approval_request,
    ) -> TurnRunResult:
        callbacks = ExecutionCallbacks(
            loop=asyncio.get_running_loop(),
            on_event=on_event,
            on_approval_request=on_approval_request,
        )
        local_turn_id = f"claude-turn:{uuid4().hex}"
        assistant_states: dict[str, dict[str, Any]] = {}
        assistant_snapshots: dict[str, AssistantMessageSnapshot] = {}
        final_status = "completed"
        final_error_message: str | None = None
        session_id = session.codex_thread_id

        async def can_use_tool(tool_name: str, tool_input: dict[str, Any], _context) -> object:
            try:
                from claude_agent_sdk import PermissionResultAllow
                from claude_agent_sdk import PermissionResultDeny
            except ImportError as exc:
                raise RuntimeError(
                    "当前环境缺少 claude-agent-sdk，请先执行 `uv sync` 安装依赖。"
                ) from exc

            response = await callbacks.on_approval_request(
                {
                    "provider": "claude",
                    "tool_name": tool_name,
                    "input": tool_input,
                    "thread_id": session_id,
                    "turn_id": local_turn_id,
                }
            )
            decision = response.get("decision")
            if decision in {"accept", "acceptForSession"}:
                return PermissionResultAllow()
            message = response.get("message") or "已拒绝当前工具调用。"
            return PermissionResultDeny(message=message)

        options = build_claude_options(
            self.settings,
            cwd=workspace.cwd,
            resume_session_id=session.codex_thread_id,
            can_use_tool=can_use_tool,
        )

        try:
            from claude_agent_sdk import AssistantMessage
            from claude_agent_sdk import ClaudeSDKClient
            from claude_agent_sdk import ResultMessage
            from claude_agent_sdk import StreamEvent
            from claude_agent_sdk import TextBlock
            from claude_agent_sdk import ToolUseBlock
        except ImportError as exc:
            raise RuntimeError(
                "当前环境缺少 claude-agent-sdk，请先执行 `uv sync` 安装依赖。"
            ) from exc

        client = ClaudeSDKClient(options=options)
        self._active_client = client
        try:
            await client.connect()
            prompt_messages = self._build_sdk_user_input(input_items, session_id=session.codex_thread_id)
            await client.query(
                self._stream_prompt_messages(prompt_messages),
                session_id=session.codex_thread_id or "default",
            )

            async for message in client.receive_messages():

                maybe_session_id = getattr(message, "session_id", None)
                if isinstance(maybe_session_id, str) and maybe_session_id:
                    if session_id is None:
                        session_id = maybe_session_id
                        await self._emit_stream_event(
                            callbacks,
                            TurnStartedEvent(thread_id=session_id, turn_id=local_turn_id),
                        )
                        self._set_active_turn(session_id, local_turn_id)

                if isinstance(message, StreamEvent):
                    if session_id is None and message.session_id:
                        session_id = message.session_id
                        await self._emit_stream_event(
                            callbacks,
                            TurnStartedEvent(thread_id=session_id, turn_id=local_turn_id),
                        )
                        self._set_active_turn(session_id, local_turn_id)
                    if session_id is not None:
                        await self._handle_stream_event(
                            callbacks,
                            session_id=session_id,
                            turn_id=local_turn_id,
                            event=message.event,
                            assistant_states=assistant_states,
                        )
                    continue

                if isinstance(message, AssistantMessage):
                    if session_id is None:
                        continue
                    for index, block in enumerate(message.content):
                        item_id = f"assistant:{len(assistant_snapshots)}:{index}"
                        if isinstance(block, TextBlock):
                            text = block.text or ""
                            if text:
                                assistant_snapshots[item_id] = AssistantMessageSnapshot(
                                    item_id=item_id,
                                    text=text,
                                )
                            continue
                        if isinstance(block, ToolUseBlock):
                            tool_name = (block.name or "").lower()
                            item_type = "mcpToolCall"
                            if tool_name == "bash":
                                item_type = "commandExecution"
                            elif tool_name in {"write", "edit", "multiedit"}:
                                item_type = "fileChange"
                            await self._emit_stream_event(
                                callbacks,
                                ItemStartedEvent(
                                    thread_id=session_id,
                                    turn_id=local_turn_id,
                                    item_id=item_id,
                                    item_type=item_type,
                                    item={
                                        "id": item_id,
                                        "type": item_type,
                                        "name": block.name,
                                        "input": block.input,
                                    },
                                ),
                            )
                            await self._emit_stream_event(
                                callbacks,
                                ItemCompletedEvent(
                                    thread_id=session_id,
                                    turn_id=local_turn_id,
                                    item_id=item_id,
                                    item_type=item_type,
                                    item={
                                        "id": item_id,
                                        "type": item_type,
                                        "name": block.name,
                                        "input": block.input,
                                    },
                                ),
                            )
                    if message.error:
                        final_status = "failed"
                        final_error_message = message.error
                    continue

                if isinstance(message, ResultMessage):
                    if message.session_id and session_id is None:
                        session_id = message.session_id
                        await self._emit_stream_event(
                            callbacks,
                            TurnStartedEvent(thread_id=session_id, turn_id=local_turn_id),
                        )
                        self._set_active_turn(session_id, local_turn_id)
                    if message.is_error:
                        final_status = "failed"
                        final_error_message = message.result or "Claude 执行失败"
                    elif message.subtype == "interrupted":
                        final_status = "interrupted"
                    break

            if session_id is None:
                raise RuntimeError("Claude 未返回 session_id")

            await self._emit_stream_event(
                callbacks,
                TurnCompletedEvent(
                    thread_id=session_id,
                    turn_id=local_turn_id,
                    status=final_status,
                    error_message=final_error_message,
                ),
            )

            final_text = "".join(snapshot.text for snapshot in assistant_snapshots.values()).strip()
            if not final_text:
                final_text = "".join(
                    value.get("text", "")
                    for _, value in sorted(assistant_states.items())
                ).strip()
            if not final_text:
                final_text = "[Claude 未返回文本结果]"
            self._known_session_ids.add(session_id)
            return TurnRunResult(
                thread_id=session_id,
                turn_id=local_turn_id,
                final_text=final_text,
                turn_status=final_status,
                error_message=final_error_message,
                assistant_messages=list(assistant_snapshots.values()),
            )
        finally:
            active_turn = self.get_active_turn()
            if active_turn is not None:
                self._clear_active_turn(active_turn.thread_id, active_turn.turn_id)
            self._active_client = None
            await client.disconnect()

    async def steer_text_turn(self, text: str) -> str:
        return await self.steer_turn(text)

    async def steer_turn(self, input_items: UserInputItems) -> str:
        del input_items
        raise RuntimeError("当前 Claude provider 暂不支持运行中追加输入，请等待当前回复完成。")

    async def interrupt_active_turn(self) -> str | None:
        active_turn = self.get_active_turn()
        if active_turn is None:
            return None
        if self._active_client is None:
            return None
        await self._active_client.interrupt()
        return active_turn.turn_id

    async def _emit_stream_event(self, callbacks: ExecutionCallbacks, event: object) -> None:
        await callbacks.on_event(event)

    async def _handle_stream_event(
        self,
        callbacks: ExecutionCallbacks,
        *,
        session_id: str,
        turn_id: str,
        event: dict[str, Any],
        assistant_states: dict[str, dict[str, Any]],
    ) -> None:
        event_type = event.get("type")
        if event_type == "content_block_start":
            index = event.get("index", 0)
            content_block = event.get("content_block") or {}
            item_id = f"stream:{index}"
            block_type = content_block.get("type")
            if block_type == "text":
                assistant_states[item_id] = {"text": ""}
                await self._emit_stream_event(
                    callbacks,
                    ItemStartedEvent(
                        thread_id=session_id,
                        turn_id=turn_id,
                        item_id=item_id,
                        item_type="agentMessage",
                        item={"id": item_id, "type": "agentMessage", "text": ""},
                    ),
                )
            return

        if event_type == "content_block_delta":
            index = event.get("index", 0)
            item_id = f"stream:{index}"
            delta = event.get("delta") or {}
            text = delta.get("text")
            if isinstance(text, str) and text:
                state = assistant_states.setdefault(item_id, {"text": ""})
                state["text"] = f"{state['text']}{text}"
                await self._emit_stream_event(
                    callbacks,
                    AgentMessageDeltaEvent(
                        thread_id=session_id,
                        turn_id=turn_id,
                        item_id=item_id,
                        delta=text,
                    ),
                )
            return

        if event_type == "content_block_stop":
            index = event.get("index", 0)
            item_id = f"stream:{index}"
            state = assistant_states.get(item_id)
            if not state:
                return
            text = state.get("text", "")
            await self._emit_stream_event(
                callbacks,
                ItemCompletedEvent(
                    thread_id=session_id,
                    turn_id=turn_id,
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": text},
                ),
            )

    def _build_sdk_user_input(
        self,
        input_items: UserInputItems,
        *,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_input_items(input_items)
        content_blocks: list[dict[str, Any]] = []
        for item in normalized:
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    content_blocks.append({"type": "text", "text": text})
                continue
            if item_type == "localImage":
                path_value = item.get("path")
                if not isinstance(path_value, str) or not path_value:
                    continue
                image_path = Path(path_value)
                media_type = self._guess_media_type(image_path)
                content_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                        },
                    }
                )
        if not content_blocks:
            raise RuntimeError("当前消息里没有可发送给 Claude 的文本或图片。")
        return [
            {
                "type": "user",
                "session_id": session_id or "default",
                "message": {
                    "role": "user",
                    "content": content_blocks,
                },
                "parent_tool_use_id": None,
            }
        ]

    async def _stream_prompt_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        for message in messages:
            yield message

    @staticmethod
    def _normalize_input_items(
        input_items: UserInputItems,
    ) -> list[dict[str, Any]]:
        if isinstance(input_items, str):
            return [{"type": "text", "text": input_items}]
        if isinstance(input_items, dict):
            return [input_items]
        return input_items

    @staticmethod
    def _guess_media_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".gif":
            return "image/gif"
        if suffix == ".webp":
            return "image/webp"
        return "image/png"

    @staticmethod
    def _timestamp_seconds(value: int | None) -> int | None:
        if value is None:
            return None
        return int(value / 1000)

    def _session_info_to_payload(self, info: object, *, fallback_cwd: str) -> dict[str, Any]:
        session_id = getattr(info, "session_id", None)
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("Claude session info 缺少 session_id")
        summary = getattr(info, "summary", None)
        cwd = getattr(info, "cwd", None) or fallback_cwd
        created_at = self._timestamp_seconds(getattr(info, "created_at", None))
        updated_at = self._timestamp_seconds(getattr(info, "last_modified", None))
        return {
            "id": session_id,
            "preview": summary if isinstance(summary, str) and summary else None,
            "status": "active",
            "cwd": cwd,
            "createdAt": created_at,
            "updatedAt": updated_at,
        }
