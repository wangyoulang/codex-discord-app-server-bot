from __future__ import annotations

from contextlib import AsyncExitStack

from codex_app_server import AskForApproval
from codex_app_server import AsyncCodex
from codex_app_server import AsyncThread
from codex_app_server import Personality
from codex_app_server import ReasoningEffort
from codex_app_server import SandboxMode
from codex_app_server import ServiceTier

from codex_discord_bot.codex.client_factory import create_async_codex
from codex_discord_bot.config import Settings
from codex_discord_bot.persistence.models import DiscordSession
from codex_discord_bot.persistence.models import Workspace


class CodexWorker:
    def __init__(self, settings: Settings, *, worker_key: str) -> None:
        self.settings = settings
        self.worker_key = worker_key
        self._exit_stack = AsyncExitStack()
        self._codex: AsyncCodex | None = None
        self._thread_cache: dict[str, AsyncThread] = {}

    async def start(self) -> None:
        if self._codex is not None:
            return
        self._codex = await self._exit_stack.enter_async_context(
            create_async_codex(self.settings)
        )

    async def close(self) -> None:
        self._thread_cache.clear()
        await self._exit_stack.aclose()
        self._codex = None

    async def ensure_thread(
        self,
        session: DiscordSession,
        workspace: Workspace,
    ) -> AsyncThread:
        await self.start()
        assert self._codex is not None

        if session.codex_thread_id and session.codex_thread_id in self._thread_cache:
            return self._thread_cache[session.codex_thread_id]

        if session.codex_thread_id:
            thread = await self._codex.thread_resume(
                session.codex_thread_id,
                cwd=workspace.cwd,
                model=workspace.default_model,
                approval_policy=AskForApproval.model_validate(workspace.approval_policy),
                sandbox=SandboxMode(workspace.sandbox_mode),
                personality=Personality(self.settings.codex_default_personality),
                service_tier=ServiceTier(self.settings.codex_service_tier),
            )
            self._thread_cache[thread.id] = thread
            return thread

        thread = await self._codex.thread_start(
            cwd=workspace.cwd,
            model=workspace.default_model,
            approval_policy=AskForApproval.model_validate(workspace.approval_policy),
            sandbox=SandboxMode(workspace.sandbox_mode),
            personality=Personality(self.settings.codex_default_personality),
            service_tier=ServiceTier(self.settings.codex_service_tier),
        )
        self._thread_cache[thread.id] = thread
        return thread

    async def run_text_turn(
        self,
        session: DiscordSession,
        workspace: Workspace,
        text: str,
    ) -> tuple[str, str]:
        thread = await self.ensure_thread(session, workspace)
        result = await thread.run(
            text,
            model=workspace.default_model,
            effort=ReasoningEffort(workspace.default_reasoning_effort),
            personality=Personality(self.settings.codex_default_personality),
            service_tier=ServiceTier(self.settings.codex_service_tier),
        )
        final_text = (result.final_response or "").strip() or "[Codex 未返回文本结果]"
        return thread.id, final_text
