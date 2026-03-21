from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading

from codex_discord_bot.codex.approvals import build_approval_envelope
from codex_discord_bot.codex.app_server_client import AppServerClient
from codex_discord_bot.codex.client_factory import build_codex_config
from codex_discord_bot.codex.stream_renderer import assistant_text_from_items
from codex_discord_bot.config import Settings
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace


@dataclass(slots=True)
class ExecutionCallbacks:
    loop: asyncio.AbstractEventLoop
    on_delta: object
    on_approval_request: object


class CodexWorker:
    def __init__(self, settings: Settings, *, worker_key: str) -> None:
        self.settings = settings
        self.worker_key = worker_key
        self._client: AppServerClient | None = None
        self._client_lock = threading.Lock()
        self._active_callbacks: ExecutionCallbacks | None = None
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
                    "model": workspace.default_model,
                    "approvalPolicy": workspace.approval_policy,
                    "sandbox": workspace.sandbox_mode,
                    "personality": self.settings.codex_default_personality,
                    "serviceTier": self.settings.codex_service_tier,
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
                "model": workspace.default_model,
                "approvalPolicy": workspace.approval_policy,
                "sandbox": workspace.sandbox_mode,
                "personality": self.settings.codex_default_personality,
                "serviceTier": self.settings.codex_service_tier,
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

    def _run_streamed_text_turn_sync(
        self,
        session: DiscordSession,
        workspace: Workspace,
        text: str,
        callbacks: ExecutionCallbacks,
    ) -> tuple[str, str, str]:
        self._active_callbacks = callbacks
        try:
            thread_id = self._ensure_thread_sync(session, workspace)
            assert self._client is not None

            params = {
                "approvalPolicy": workspace.approval_policy,
                "cwd": workspace.cwd,
                "effort": workspace.default_reasoning_effort,
                "model": workspace.default_model,
                "personality": self.settings.codex_default_personality,
                "sandboxPolicy": {"type": "workspaceWrite", "networkAccess": False},
                "serviceTier": self.settings.codex_service_tier,
            }
            started = self._client.turn_start(thread_id, text, params=params)
            started_turn = started.get("turn")
            if not isinstance(started_turn, dict):
                raise RuntimeError("turn/start 响应缺少 turn")
            turn_id = started_turn.get("id")
            if not isinstance(turn_id, str) or not turn_id:
                raise RuntimeError("turn/start 响应缺少 turn.id")

            while True:
                notification = self._client.next_notification()
                payload = notification.payload
                if (
                    notification.method == "item/agentMessage/delta"
                    and payload.get("turnId") == turn_id
                ):
                    delta = payload.get("delta")
                    if isinstance(delta, str) and delta:
                        future = asyncio.run_coroutine_threadsafe(
                            callbacks.on_delta(delta),
                            callbacks.loop,
                        )
                        future.result()
                    continue

                if (
                    notification.method == "turn/completed"
                    and isinstance(payload.get("turn"), dict)
                    and payload["turn"].get("id") == turn_id
                ):
                    break

            thread_snapshot = self._client.thread_read(thread_id, include_turns=True)
            persisted_turn = None
            thread_payload = thread_snapshot.get("thread") or {}
            for turn in thread_payload.get("turns") or []:
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    persisted_turn = turn
                    break

            final_text = assistant_text_from_items(
                persisted_turn.get("items") if isinstance(persisted_turn, dict) else None
            ).strip() or "[Codex 未返回文本结果]"
            return thread_id, turn_id, final_text
        finally:
            self._active_callbacks = None

    async def run_streamed_text_turn(
        self,
        session: DiscordSession,
        workspace: Workspace,
        text: str,
        *,
        on_delta,
        on_approval_request,
    ) -> tuple[str, str, str]:
        callbacks = ExecutionCallbacks(
            loop=asyncio.get_running_loop(),
            on_delta=on_delta,
            on_approval_request=on_approval_request,
        )
        return await asyncio.to_thread(
            self._run_streamed_text_turn_sync,
            session,
            workspace,
            text,
            callbacks,
        )
