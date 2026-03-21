from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.enums import PendingRequestType
from codex_discord_bot.persistence.models import PendingRequest
from codex_discord_bot.persistence.repositories.pending_requests import PendingRequestRepository


@dataclass(slots=True)
class PendingApprovalHandle:
    local_request_id: str
    request_type: str
    title: str
    body: str
    decisions: tuple[str, ...]
    response_payloads: dict[str, dict[str, Any]]
    requester_id: str | None
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    future: asyncio.Future[dict[str, Any]] = field(repr=False)
    message_id: str | None = None


class ApprovalService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._handles: dict[str, PendingApprovalHandle] = {}
        self._lock = asyncio.Lock()

    async def register_request(
        self,
        *,
        local_request_id: str,
        request_type: str,
        title: str,
        body: str,
        decisions: tuple[str, ...],
        response_payloads: dict[str, dict[str, Any]],
        requester_id: str | None,
        thread_id: str | None,
        turn_id: str | None,
        item_id: str | None,
    ) -> PendingApprovalHandle:
        async with self._lock:
            handle = PendingApprovalHandle(
                local_request_id=local_request_id,
                request_type=request_type,
                title=title,
                body=body,
                decisions=decisions,
                response_payloads=response_payloads,
                requester_id=requester_id,
                thread_id=thread_id,
                turn_id=turn_id,
                item_id=item_id,
                future=asyncio.get_running_loop().create_future(),
            )
            self._handles[local_request_id] = handle

        async with self.db.session() as session:
            repo = PendingRequestRepository(session)
            await repo.create(
                PendingRequest(
                    request_id=local_request_id,
                    discord_thread_id=thread_id or "",
                    codex_thread_id=None,
                    turn_id=turn_id,
                    item_id=item_id,
                    request_type=PendingRequestType(request_type),
                    available_decisions_json=list(decisions),
                )
            )
        return handle

    async def set_message_id(self, local_request_id: str, message_id: str) -> None:
        async with self._lock:
            handle = self._handles.get(local_request_id)
            if handle is not None:
                handle.message_id = message_id
        async with self.db.session() as session:
            repo = PendingRequestRepository(session)
            await repo.update_message_id(local_request_id, message_id)

    async def get_request(self, local_request_id: str) -> PendingApprovalHandle | None:
        async with self._lock:
            return self._handles.get(local_request_id)

    async def can_resolve(
        self,
        local_request_id: str,
        *,
        actor_id: str,
        can_manage: bool,
    ) -> bool:
        handle = await self.get_request(local_request_id)
        if handle is None:
            return False
        if can_manage:
            return True
        if handle.requester_id is None:
            return True
        return handle.requester_id == actor_id

    async def resolve_request(
        self,
        local_request_id: str,
        *,
        decision: str,
        actor_id: str | None = None,
    ) -> bool:
        async with self._lock:
            handle = self._handles.get(local_request_id)
            if handle is None or handle.future.done():
                return False
            response_payload = handle.response_payloads.get(decision)
            if response_payload is None:
                return False
            handle.future.set_result(
                {
                    "decision": decision,
                    "actor_id": actor_id,
                    "response": response_payload,
                }
            )
            return True

    async def wait_for_decision(
        self,
        local_request_id: str,
        *,
        timeout_seconds: float = 900.0,
    ) -> dict[str, Any]:
        handle = await self.get_request(local_request_id)
        if handle is None:
            raise ValueError("审批请求不存在")
        return await asyncio.wait_for(handle.future, timeout=timeout_seconds)

    async def cleanup_request(self, local_request_id: str) -> None:
        async with self._lock:
            self._handles.pop(local_request_id, None)
        async with self.db.session() as session:
            repo = PendingRequestRepository(session)
            await repo.delete_by_request_id(local_request_id)
