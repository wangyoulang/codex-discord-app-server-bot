from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TurnStartedEvent:
    thread_id: str
    turn_id: str


@dataclass(slots=True)
class ItemStartedEvent:
    thread_id: str
    turn_id: str
    item_id: str
    item_type: str
    item: dict[str, Any]


@dataclass(slots=True)
class AgentMessageDeltaEvent:
    thread_id: str
    turn_id: str
    item_id: str
    delta: str


@dataclass(slots=True)
class ItemCompletedEvent:
    thread_id: str
    turn_id: str
    item_id: str
    item_type: str
    item: dict[str, Any]


@dataclass(slots=True)
class TurnCompletedEvent:
    thread_id: str
    turn_id: str
    status: str
    error_message: str | None = None


CodexStreamEvent = (
    TurnStartedEvent
    | ItemStartedEvent
    | AgentMessageDeltaEvent
    | ItemCompletedEvent
    | TurnCompletedEvent
)
