from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.codex.approvals import build_approval_envelope
from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.repositories.pending_requests import PendingRequestRepository
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.services.approval_service import ApprovalService


def test_build_command_approval_envelope() -> None:
    envelope = build_approval_envelope(
        "item/commandExecution/requestApproval",
        {
            "threadId": "thr_1",
            "turnId": "turn_1",
            "itemId": "item_1",
            "command": "pytest -q",
            "cwd": "/repo",
            "reason": "Run tests",
            "availableDecisions": ["accept", "decline", "cancel"],
        },
    )
    assert envelope.request_type == "command_execution"
    assert envelope.thread_id == "thr_1"
    assert envelope.turn_id == "turn_1"
    assert "pytest -q" in envelope.body
    assert envelope.decisions == ("accept", "decline", "cancel")
    assert envelope.response_payloads["accept"] == {"decision": "accept"}


def test_build_permissions_approval_envelope() -> None:
    envelope = build_approval_envelope(
        "item/permissions/requestApproval",
        {
            "threadId": "thr_1",
            "turnId": "turn_1",
            "itemId": "item_1",
            "reason": "需要读写工作区",
            "permissions": {
                "fileSystem": {
                    "read": ["/repo"],
                    "write": ["/repo"],
                },
                "network": None,
            },
        },
    )
    assert envelope.request_type == "permissions"
    assert envelope.decisions == ("accept", "acceptForSession", "decline")
    assert envelope.response_payloads["accept"] == {
        "permissions": {
            "fileSystem": {
                "read": ["/repo"],
                "write": ["/repo"],
            },
            "network": None,
        },
        "scope": "turn",
    }
    assert envelope.response_payloads["acceptForSession"]["scope"] == "session"
    assert envelope.response_payloads["decline"] == {"permissions": {}, "scope": "turn"}


def test_approval_service_roundtrip(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        service = ApprovalService(db)
        handle = await service.register_request(
            local_request_id="req_local_1",
            request_type="command_execution",
            title="命令执行审批",
            body="body",
            decisions=("accept", "decline"),
            response_payloads={
                "accept": {"decision": "accept"},
                "decline": {"decision": "decline"},
            },
            requester_id="u1",
            thread_id="discord_thread_1",
            turn_id="turn_1",
            item_id="item_1",
        )
        assert handle.local_request_id == "req_local_1"
        assert await service.can_resolve("req_local_1", actor_id="u1", can_manage=False) is True
        assert await service.can_resolve("req_local_1", actor_id="u2", can_manage=False) is False

        waiter = asyncio.create_task(service.wait_for_decision("req_local_1", timeout_seconds=1))
        await service.resolve_request("req_local_1", decision="accept", actor_id="u1")
        result = await waiter
        assert result["decision"] == "accept"
        assert result["response"] == {"decision": "accept"}

        assert (
            await service.resolve_request("req_local_1", decision="decline", actor_id="u1") is False
        )

        async with db.session() as session:
            repo = PendingRequestRepository(session)
            assert await repo.get_by_request_id("req_local_1") is not None

        await service.cleanup_request("req_local_1")
        async with db.session() as session:
            repo = PendingRequestRepository(session)
            assert await repo.get_by_request_id("req_local_1") is None

        await db.close()

    asyncio.run(scenario())
