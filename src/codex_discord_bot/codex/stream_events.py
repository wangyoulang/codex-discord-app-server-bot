from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codex_discord_bot.codex.token_usage import TokenUsageSnapshot


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


@dataclass(slots=True)
class TokenUsageUpdatedEvent:
    snapshot: TokenUsageSnapshot

    @property
    def thread_id(self) -> str:
        return self.snapshot.thread_id

    @property
    def turn_id(self) -> str:
        return self.snapshot.turn_id


CodexStreamEvent = (
    TurnStartedEvent
    | ItemStartedEvent
    | AgentMessageDeltaEvent
    | ItemCompletedEvent
    | TurnCompletedEvent
    | TokenUsageUpdatedEvent
)
