from __future__ import annotations

from typing import Any

from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.models import AuditEvent
from codex_discord_bot.persistence.repositories.audit_events import AuditEventRepository


class AuditService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(
        self,
        *,
        action: str,
        guild_id: str | None = None,
        discord_thread_id: str | None = None,
        actor_id: str | None = None,
        payload: dict[str, Any] | list[Any] | None = None,
    ) -> AuditEvent:
        async with self.db.session() as session:
            repo = AuditEventRepository(session)
            event = AuditEvent(
                guild_id=guild_id,
                discord_thread_id=discord_thread_id,
                actor_id=actor_id,
                action=action,
                payload_json=payload,
            )
            return await repo.create(event)
