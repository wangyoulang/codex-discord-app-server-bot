from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any

from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.models import CodexThread
from codex_discord_bot.persistence.repositories.codex_threads import CodexThreadRepository
from codex_discord_bot.utils.time import utc_now


class CodexThreadService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_by_codex_thread_id(self, codex_thread_id: str) -> CodexThread | None:
        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            return await repo.get_by_codex_thread_id(codex_thread_id)

    async def bind_thread_to_discord(
        self,
        *,
        codex_thread_id: str,
        workspace_id: int,
        discord_thread_id: str,
    ) -> CodexThread:
        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            record = await repo.get_by_codex_thread_id(codex_thread_id)
            if record is None:
                record = CodexThread(
                    codex_thread_id=codex_thread_id,
                    workspace_id=workspace_id,
                    source_label="unknown",
                    archived=False,
                    thread_status="unknown",
                    bound_discord_thread_id=discord_thread_id,
                )
                return await repo.create(record)

            if record.workspace_id != workspace_id:
                raise ValueError("目标会话不属于当前工作区，无法恢复。")
            record.bound_discord_thread_id = discord_thread_id
            record.updated_at = utc_now()
            if not record.source_label:
                record.source_label = "unknown"
            return await repo.save(record)

    async def set_archived_state(
        self,
        *,
        codex_thread_id: str,
        archived: bool,
    ) -> CodexThread:
        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            record = await repo.get_by_codex_thread_id(codex_thread_id)
            if record is None:
                raise ValueError("Codex 会话不存在，无法更新归档状态。")
            record.archived = archived
            record.updated_at = utc_now()
            return await repo.save(record)

    async def list_for_workspace(
        self,
        *,
        workspace_id: int,
        scope: str,
        query: str | None = None,
        archived: bool = False,
        limit: int = 10,
    ) -> list[CodexThread]:
        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            source_label = "discord-bot" if scope == "bot" else None
            return await repo.list_for_workspace(
                workspace_id=workspace_id,
                source_label=source_label,
                query=query,
                archived=archived,
                limit=limit,
            )

    async def sync_thread_from_payload(
        self,
        *,
        workspace_id: int,
        thread_payload: dict[str, Any],
        archived: bool,
        source_override: object | None = None,
    ) -> CodexThread:
        codex_thread_id = thread_payload.get("id")
        if not isinstance(codex_thread_id, str) or not codex_thread_id:
            raise ValueError("thread payload 缺少 id")

        source_kind, source_label = _normalize_thread_source(
            source_override if source_override is not None else thread_payload.get("source")
        )
        preview = thread_payload.get("preview")
        status = _normalize_thread_status(thread_payload.get("status"))
        thread_created_at = _timestamp_to_utc(thread_payload.get("createdAt"))
        thread_updated_at = _timestamp_to_utc(thread_payload.get("updatedAt"))

        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            record = await repo.get_by_codex_thread_id(codex_thread_id)
            if record is None:
                record = CodexThread(
                    codex_thread_id=codex_thread_id,
                    workspace_id=workspace_id,
                    source_kind=source_kind,
                    source_label=source_label,
                    preview=preview if isinstance(preview, str) and preview else None,
                    archived=archived,
                    thread_status=status,
                    thread_created_at=thread_created_at,
                    thread_updated_at=thread_updated_at,
                )
                return await repo.create(record)

            record.workspace_id = workspace_id
            if source_override is not None:
                record.source_kind = source_kind or record.source_kind
                record.source_label = source_label or record.source_label
            elif record.source_label != "discord-bot":
                record.source_kind = source_kind or record.source_kind
                record.source_label = source_label or record.source_label
            if isinstance(preview, str) and preview:
                record.preview = preview
            record.archived = archived
            record.thread_status = status
            record.thread_created_at = thread_created_at or record.thread_created_at
            record.thread_updated_at = thread_updated_at or record.thread_updated_at
            return await repo.save(record)

    async def sync_threads_from_payloads(
        self,
        *,
        workspace_id: int,
        thread_payloads: list[dict[str, Any]],
        archived: bool,
    ) -> list[CodexThread]:
        records: list[CodexThread] = []
        for payload in thread_payloads:
            try:
                records.append(
                    await self.sync_thread_from_payload(
                        workspace_id=workspace_id,
                        thread_payload=payload,
                        archived=archived,
                    )
                )
            except ValueError:
                continue
        return records

    async def ensure_thread_available_for_discord(
        self,
        *,
        workspace_id: int,
        codex_thread_id: str,
        discord_thread_id: str,
    ) -> CodexThread:
        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            record = await repo.get_by_codex_thread_id(codex_thread_id)
            if record is None:
                record = CodexThread(
                    codex_thread_id=codex_thread_id,
                    workspace_id=workspace_id,
                    source_label="unknown",
                    archived=False,
                    thread_status="unknown",
                    bound_discord_thread_id=discord_thread_id,
                )
                return await repo.create(record)

            if (
                record.bound_discord_thread_id is not None
                and record.bound_discord_thread_id != discord_thread_id
            ):
                raise ValueError(
                    f"目标会话当前已绑定 Discord 线程 `{record.bound_discord_thread_id}`，默认不允许跨线程接管。"
                )
            if record.workspace_id != workspace_id:
                raise ValueError("目标会话不属于当前工作区，无法恢复。")

            record.bound_discord_thread_id = discord_thread_id
            if not record.source_label:
                record.source_label = "unknown"
            record.updated_at = utc_now()
            return await repo.save(record)

    async def release_binding_if_owned(
        self,
        *,
        codex_thread_id: str,
        discord_thread_id: str,
    ) -> CodexThread | None:
        async with self.db.session() as session:
            repo = CodexThreadRepository(session)
            record = await repo.get_by_codex_thread_id(codex_thread_id)
            if record is None:
                return None
            if record.bound_discord_thread_id != discord_thread_id:
                return record
            record.bound_discord_thread_id = None
            record.updated_at = utc_now()
            return await repo.save(record)


def _normalize_thread_source(source: object) -> tuple[str | None, str | None]:
    if isinstance(source, str) and source:
        return source, source
    if isinstance(source, dict):
        custom_value = source.get("custom")
        if isinstance(custom_value, str) and custom_value:
            return "custom", custom_value
    return None, None


def _normalize_thread_status(status: object) -> str:
    if isinstance(status, dict):
        status_type = status.get("type")
        if isinstance(status_type, str) and status_type:
            return status_type
    if isinstance(status, str) and status:
        return status
    return "unknown"


def _timestamp_to_utc(value: object) -> datetime | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value, UTC)
