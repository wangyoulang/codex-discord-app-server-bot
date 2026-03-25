from __future__ import annotations

from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codex_discord_bot.persistence.models import CodexThread
from codex_discord_bot.providers.types import ProviderKind


class CodexThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, value: CodexThread) -> CodexThread:
        self.session.add(value)
        await self.session.flush()
        await self.session.refresh(value)
        return value

    async def save(self, record: CodexThread) -> CodexThread:
        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def get_by_provider_thread_id(
        self,
        provider_thread_id: str,
        *,
        provider: ProviderKind,
    ) -> CodexThread | None:
        stmt = select(CodexThread).where(
            CodexThread.codex_thread_id == provider_thread_id,
            CodexThread.provider == provider,
        )
        return await self.session.scalar(stmt)

    async def list_for_workspace(
        self,
        *,
        workspace_id: int,
        provider: ProviderKind,
        source_label: str | None = None,
        query: str | None = None,
        archived: bool | None = None,
        limit: int = 10,
    ) -> list[CodexThread]:
        stmt = select(CodexThread).where(
            CodexThread.workspace_id == workspace_id,
            CodexThread.provider == provider,
        )
        if source_label is not None:
            stmt = stmt.where(CodexThread.source_label == source_label)
        if archived is not None:
            stmt = stmt.where(CodexThread.archived == archived)
        if query:
            like_value = f"%{query.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(CodexThread.codex_thread_id).like(like_value),
                    func.lower(func.coalesce(CodexThread.preview, "")).like(like_value),
                    func.lower(func.coalesce(CodexThread.source_label, "")).like(like_value),
                )
            )
        stmt = stmt.order_by(
            func.coalesce(CodexThread.thread_updated_at, CodexThread.updated_at).desc(),
            CodexThread.updated_at.desc(),
        ).limit(limit)
        rows = await self.session.scalars(stmt)
        return list(rows.all())
