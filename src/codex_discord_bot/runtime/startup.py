from __future__ import annotations

import asyncio
from dataclasses import dataclass

from codex_discord_bot.claude.client_factory import validate_claude_runtime
from codex_discord_bot.config import Settings
from codex_discord_bot.config import load_settings
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.services.approval_service import ApprovalService
from codex_discord_bot.services.artifact_service import ArtifactService
from codex_discord_bot.services.audit_service import AuditService
from codex_discord_bot.services.codex_thread_service import CodexThreadService
from codex_discord_bot.services.review_service import ReviewService
from codex_discord_bot.services.session_service import SessionService
from codex_discord_bot.services.turn_output_service import TurnOutputService
from codex_discord_bot.services.workspace_service import WorkspaceService


@dataclass(slots=True)
class ApplicationContext:
    settings: Settings
    db: Database
    workspace_service: WorkspaceService
    session_service: SessionService
    turn_output_service: TurnOutputService
    approval_service: ApprovalService
    review_service: ReviewService
    artifact_service: ArtifactService
    codex_thread_service: CodexThreadService
    audit_service: AuditService
    worker_pool: object | None = None
    session_router: object | None = None
    background_tasks: list[asyncio.Task] | None = None
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        for task in self.background_tasks or []:
            task.cancel()
        for task in self.background_tasks or []:
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self.worker_pool is not None:
            close_all = getattr(self.worker_pool, "close_all", None)
            if close_all is not None:
                await close_all()

        await self.db.close()


async def build_application_context() -> ApplicationContext:
    settings = load_settings()
    validate_claude_runtime(settings)
    db = Database(settings.database_url)

    return ApplicationContext(
        settings=settings,
        db=db,
        workspace_service=WorkspaceService(db),
        session_service=SessionService(db),
        turn_output_service=TurnOutputService(db),
        approval_service=ApprovalService(db),
        review_service=ReviewService(),
        artifact_service=ArtifactService(settings.artifact_dir),
        codex_thread_service=CodexThreadService(db),
        audit_service=AuditService(db),
        background_tasks=[],
    )
