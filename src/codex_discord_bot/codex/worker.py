from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field
import threading
from typing import Any

from codex_discord_bot.codex.approvals import build_approval_envelope
from codex_discord_bot.codex.app_server_client import AppServerClient
from codex_discord_bot.codex.client_factory import build_codex_config
from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import CodexStreamEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_events import TurnCompletedEvent
from codex_discord_bot.codex.stream_events import TurnStartedEvent
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.stream_renderer import assistant_messages_from_items
from codex_discord_bot.codex.stream_renderer import assistant_text_from_items
from codex_discord_bot.config import Settings
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace


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


class CodexWorker:
    def __init__(self, settings: Settings, *, worker_key: str) -> None:
        self.settings = settings
        self.worker_key = worker_key
        self._client: AppServerClient | None = None
        self._client_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_callbacks: ExecutionCallbacks | None = None
        self._active_turn: ActiveTurn | None = None
        self._thread_cache: dict[str, str] = {}

    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_client_sync)

    async def close(self) -> None:
        self._thread_cache.clear()
        await asyncio.to_thread(self._close_client_sync)

    def _ensure_client_sync(self) -> None:
        if self._client is not None:
            return
        with self._client_lock:
            if self._client is not None:
                return
            self._client = AppServerClient(
                config=build_codex_config(self.settings),
                approval_handler=self._approval_handler,
            )
            self._client.start()
            self._client.initialize()

    def _close_client_sync(self) -> None:
        with self._client_lock:
            if self._client is None:
                return
            self._client.close()
            self._client = None

    def _approval_handler(self, method: str, params: dict | None) -> dict:
        callbacks = self._active_callbacks
        if callbacks is None:
            return {"decision": "decline"}

        envelope = build_approval_envelope(method, params)
        future = asyncio.run_coroutine_threadsafe(
            callbacks.on_approval_request(envelope),
            callbacks.loop,
        )
        return future.result()

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

    def _ensure_thread_sync(
        self,
        session: DiscordSession,
        workspace: Workspace,
    ) -> str:
        self._ensure_client_sync()
        assert self._client is not None

        if session.codex_thread_id and session.codex_thread_id in self._thread_cache:
            return self._thread_cache[session.codex_thread_id]

        if session.codex_thread_id:
            response = self._client.thread_resume(
                session.codex_thread_id,
                {
                    "cwd": workspace.cwd,
                },
            )
            thread = response.get("thread")
            if not isinstance(thread, dict):
                raise RuntimeError("thread/resume 响应缺少 thread")
            thread_id = thread.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise RuntimeError("thread/resume 响应缺少 thread.id")
            self._thread_cache[thread_id] = thread_id
            return thread_id

        response = self._client.thread_start(
            {
                "cwd": workspace.cwd,
            }
        )
        thread = response.get("thread")
        if not isinstance(thread, dict):
            raise RuntimeError("thread/start 响应缺少 thread")
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("thread/start 响应缺少 thread.id")
        self._thread_cache[thread_id] = thread_id
        return thread_id

    async def ensure_thread(
        self,
        session: DiscordSession,
        workspace: Workspace,
    ) -> str:
        return await asyncio.to_thread(self._ensure_thread_sync, session, workspace)

    def _list_threads_sync(
        self,
        *,
        cwd: str,
        limit: int,
        search_term: str | None,
        archived: bool,
    ) -> list[dict[str, Any]]:
        self._ensure_client_sync()
        assert self._client is not None

        params: dict[str, Any] = {
            "cwd": cwd,
            "limit": limit,
            "archived": archived,
        }
        if search_term:
            params["searchTerm"] = search_term
        result = self._client.thread_list(params)
        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError("thread/list 响应缺少 data")
        return [item for item in data if isinstance(item, dict)]

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int = 10,
        search_term: str | None = None,
        archived: bool = False,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_threads_sync,
            cwd=cwd,
            limit=limit,
            search_term=search_term,
            archived=archived,
        )

    def _read_thread_sync(
        self,
        thread_id: str,
        *,
        include_turns: bool,
    ) -> dict[str, Any]:
        self._ensure_client_sync()
        assert self._client is not None
        result = self._client.thread_read(thread_id, include_turns=include_turns)
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise RuntimeError("thread/read 响应缺少 thread")
        return thread

    async def read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._read_thread_sync,
            thread_id,
            include_turns=include_turns,
        )

    def _run_streamed_turn_sync(
        self,
        session: DiscordSession,
        workspace: Workspace,
        input_items: UserInputItems,
        callbacks: ExecutionCallbacks,
    ) -> TurnRunResult:
        self._active_callbacks = callbacks
        try:
            thread_id = self._ensure_thread_sync(session, workspace)
            assert self._client is not None

            params = {"cwd": workspace.cwd}
            started = self._client.turn_start(thread_id, input_items, params=params)
            started_turn = started.get("turn")
            if not isinstance(started_turn, dict):
                raise RuntimeError("turn/start 响应缺少 turn")
            turn_id = started_turn.get("id")
            if not isinstance(turn_id, str) or not turn_id:
                raise RuntimeError("turn/start 响应缺少 turn.id")
            self._set_active_turn(thread_id, turn_id)

            completed_status = "completed"
            completed_error_message: str | None = None

            while True:
                notification = self._client.next_notification()
                payload = notification.payload
                if notification.method == "turn/started":
                    turn_payload = payload.get("turn")
                    if isinstance(turn_payload, dict) and turn_payload.get("id") == turn_id:
                        self._emit_stream_event(
                            callbacks,
                            TurnStartedEvent(thread_id=thread_id, turn_id=turn_id),
                        )
                    continue

                if notification.method == "item/started" and payload.get("turnId") == turn_id:
                    item_event = self._build_item_event(payload, event_type="started")
                    if item_event is not None:
                        self._emit_stream_event(callbacks, item_event)
                    continue

                if (
                    notification.method == "item/agentMessage/delta"
                    and payload.get("turnId") == turn_id
                ):
                    delta = payload.get("delta")
                    item_id = payload.get("itemId")
                    if isinstance(delta, str) and delta:
                        self._emit_stream_event(
                            callbacks,
                            AgentMessageDeltaEvent(
                                thread_id=thread_id,
                                turn_id=turn_id,
                                item_id=str(item_id or ""),
                                delta=delta,
                            ),
                        )
                    continue

                if notification.method == "item/completed" and payload.get("turnId") == turn_id:
                    item_event = self._build_item_event(payload, event_type="completed")
                    if item_event is not None:
                        self._emit_stream_event(callbacks, item_event)
                    continue

                if (
                    notification.method == "turn/completed"
                    and isinstance(payload.get("turn"), dict)
                    and payload["turn"].get("id") == turn_id
                ):
                    turn_payload = payload["turn"]
                    status = turn_payload.get("status")
                    if isinstance(status, str) and status:
                        completed_status = status
                    error = turn_payload.get("error")
                    if isinstance(error, dict):
                        message = error.get("message")
                        if isinstance(message, str) and message:
                            completed_error_message = message
                    self._emit_stream_event(
                        callbacks,
                        TurnCompletedEvent(
                            thread_id=thread_id,
                            turn_id=turn_id,
                            status=completed_status,
                            error_message=completed_error_message,
                        ),
                    )
                    break

            thread_snapshot = self._client.thread_read(thread_id, include_turns=True)
            persisted_turn = None
            thread_payload = thread_snapshot.get("thread") or {}
            for turn in thread_payload.get("turns") or []:
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    persisted_turn = turn
                    break

            assistant_messages = assistant_messages_from_items(
                persisted_turn.get("items") if isinstance(persisted_turn, dict) else None
            )
            final_text = assistant_text_from_items(
                persisted_turn.get("items") if isinstance(persisted_turn, dict) else None
            ).strip() or "[Codex 未返回文本结果]"
            return TurnRunResult(
                thread_id=thread_id,
                turn_id=turn_id,
                final_text=final_text,
                turn_status=completed_status,
                error_message=completed_error_message,
                assistant_messages=assistant_messages,
            )
        finally:
            active_turn = self.get_active_turn()
            if active_turn is not None:
                self._clear_active_turn(active_turn.thread_id, active_turn.turn_id)
            self._active_callbacks = None

    def _run_streamed_text_turn_sync(
        self,
        session: DiscordSession,
        workspace: Workspace,
        text: str,
        callbacks: ExecutionCallbacks,
    ) -> TurnRunResult:
        return self._run_streamed_turn_sync(session, workspace, text, callbacks)

    def _emit_stream_event(
        self,
        callbacks: ExecutionCallbacks,
        event: CodexStreamEvent,
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(
            callbacks.on_event(event),
            callbacks.loop,
        )
        future.result()

    def _build_item_event(
        self,
        payload: dict,
        *,
        event_type: str,
    ) -> ItemStartedEvent | ItemCompletedEvent | None:
        raw_item = payload.get("item")
        if not isinstance(raw_item, dict):
            return None
        item_id = raw_item.get("id")
        item_type = raw_item.get("type")
        thread_id = payload.get("threadId")
        turn_id = payload.get("turnId")
        if not all(isinstance(value, str) and value for value in (item_id, item_type, thread_id, turn_id)):
            return None

        if event_type == "started":
            return ItemStartedEvent(
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
                item_type=item_type,
                item=raw_item,
            )
        return ItemCompletedEvent(
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            item_type=item_type,
            item=raw_item,
        )

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
        return await asyncio.to_thread(
            self._run_streamed_turn_sync,
            session,
            workspace,
            input_items,
            callbacks,
        )

    def _steer_turn_sync(self, input_items: UserInputItems) -> str:
        active_turn = self.get_active_turn()
        if active_turn is None:
            raise RuntimeError("当前没有运行中的 turn")

        self._ensure_client_sync()
        assert self._client is not None
        response = self._client.turn_steer(
            active_turn.thread_id,
            input_items,
            expected_turn_id=active_turn.turn_id,
        )
        turn_id = response.get("turnId")
        if isinstance(turn_id, str) and turn_id:
            return turn_id
        return active_turn.turn_id

    def _steer_text_turn_sync(self, text: str) -> str:
        return self._steer_turn_sync(text)

    async def steer_text_turn(self, text: str) -> str:
        return await self.steer_turn(text)

    async def steer_turn(self, input_items: UserInputItems) -> str:
        return await asyncio.to_thread(self._steer_turn_sync, input_items)

    def _interrupt_active_turn_sync(self) -> str | None:
        active_turn = self.get_active_turn()
        if active_turn is None:
            return None

        self._ensure_client_sync()
        assert self._client is not None
        self._client.turn_interrupt(active_turn.thread_id, active_turn.turn_id)
        return active_turn.turn_id

    async def interrupt_active_turn(self) -> str | None:
        return await asyncio.to_thread(self._interrupt_active_turn_sync)
