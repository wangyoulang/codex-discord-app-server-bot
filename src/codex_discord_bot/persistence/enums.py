from __future__ import annotations

from enum import Enum


class SessionStatus(str, Enum):
    uninitialized = "uninitialized"
    ready = "ready"
    running = "running"
    error = "error"


class TurnOutputState(str, Enum):
    pending = "pending"
    previewing = "previewing"
    finalizing = "finalizing"
    completed = "completed"
    interrupted = "interrupted"
    failed = "failed"


class PendingRequestType(str, Enum):
    command_execution = "command_execution"
    file_change = "file_change"
    permissions = "permissions"
    tool_input = "tool_input"
