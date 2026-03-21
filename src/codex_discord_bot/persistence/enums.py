from __future__ import annotations

from enum import Enum


class SessionStatus(str, Enum):
    ready = "ready"
    running = "running"
    error = "error"


class PendingRequestType(str, Enum):
    command_execution = "command_execution"
    file_change = "file_change"
    permissions = "permissions"
    tool_input = "tool_input"
