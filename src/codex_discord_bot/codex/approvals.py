from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from codex_discord_bot.utils.text import as_code_block


SUPPORTED_DECISIONS = ("accept", "acceptForSession", "decline", "cancel")


@dataclass(slots=True)
class ApprovalEnvelope:
    local_request_id: str
    request_type: str
    method: str
    title: str
    body: str
    decisions: tuple[str, ...]
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    response_payloads: dict[str, dict[str, Any]]


def _extract_available_decisions(params: dict[str, object]) -> tuple[str, ...]:
    raw = params.get("availableDecisions")
    if not isinstance(raw, list):
        return SUPPORTED_DECISIONS

    values: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in SUPPORTED_DECISIONS:
            values.append(item)
            continue
        if isinstance(item, dict):
            for key in item:
                if key in SUPPORTED_DECISIONS:
                    values.append(key)
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    if not seen:
        return ("decline", "cancel")
    return tuple(seen)


def build_approval_envelope(method: str, params: dict[str, object] | None) -> ApprovalEnvelope:
    payload = params or {}
    local_request_id = uuid4().hex
    decisions = _extract_available_decisions(payload)
    thread_id = payload.get("threadId")
    turn_id = payload.get("turnId")
    item_id = payload.get("itemId")

    if method == "item/commandExecution/requestApproval":
        command = payload.get("command")
        cwd = payload.get("cwd")
        reason = payload.get("reason")
        parts = ["Codex 请求执行命令审批。"]
        if isinstance(command, str) and command:
            parts.append(as_code_block(command))
        if isinstance(cwd, str) and cwd:
            parts.append(f"cwd: `{cwd}`")
        if isinstance(reason, str) and reason:
            parts.append(f"原因：{reason}")
        return ApprovalEnvelope(
            local_request_id=local_request_id,
            request_type="command_execution",
            method=method,
            title="命令执行审批",
            body="\n".join(parts),
            decisions=decisions,
            thread_id=str(thread_id) if isinstance(thread_id, str) else None,
            turn_id=str(turn_id) if isinstance(turn_id, str) else None,
            item_id=str(item_id) if isinstance(item_id, str) else None,
            response_payloads={decision: {"decision": decision} for decision in decisions},
        )

    if method == "item/fileChange/requestApproval":
        reason = payload.get("reason")
        grant_root = payload.get("grantRoot")
        parts = ["Codex 请求应用文件修改。"]
        if isinstance(reason, str) and reason:
            parts.append(f"原因：{reason}")
        if isinstance(grant_root, str) and grant_root:
            parts.append(f"授权根目录：`{grant_root}`")
        return ApprovalEnvelope(
            local_request_id=local_request_id,
            request_type="file_change",
            method=method,
            title="文件修改审批",
            body="\n".join(parts),
            decisions=decisions,
            thread_id=str(thread_id) if isinstance(thread_id, str) else None,
            turn_id=str(turn_id) if isinstance(turn_id, str) else None,
            item_id=str(item_id) if isinstance(item_id, str) else None,
            response_payloads={decision: {"decision": decision} for decision in decisions},
        )

    if method == "item/permissions/requestApproval":
        reason = payload.get("reason")
        permissions = payload.get("permissions")
        parts = ["Codex 请求额外权限。"]
        if isinstance(reason, str) and reason:
            parts.append(f"原因：{reason}")
        if permissions is not None:
            parts.append(as_code_block(str(permissions)))

        granted_permissions = permissions if isinstance(permissions, dict) else {}
        decisions = ("accept", "acceptForSession", "decline")
        return ApprovalEnvelope(
            local_request_id=local_request_id,
            request_type="permissions",
            method=method,
            title="权限审批",
            body="\n".join(parts),
            decisions=decisions,
            thread_id=str(thread_id) if isinstance(thread_id, str) else None,
            turn_id=str(turn_id) if isinstance(turn_id, str) else None,
            item_id=str(item_id) if isinstance(item_id, str) else None,
            response_payloads={
                "accept": {"permissions": granted_permissions, "scope": "turn"},
                "acceptForSession": {
                    "permissions": granted_permissions,
                    "scope": "session",
                },
                "decline": {"permissions": {}, "scope": "turn"},
            },
        )

    return ApprovalEnvelope(
        local_request_id=local_request_id,
        request_type="tool_input",
        method=method,
        title="未知审批请求",
        body=f"收到未适配的审批方法：`{method}`",
        decisions=("decline", "cancel"),
        thread_id=str(thread_id) if isinstance(thread_id, str) else None,
        turn_id=str(turn_id) if isinstance(turn_id, str) else None,
        item_id=str(item_id) if isinstance(item_id, str) else None,
        response_payloads={
            "decline": {"decision": "decline"},
            "cancel": {"decision": "cancel"},
        },
    )
