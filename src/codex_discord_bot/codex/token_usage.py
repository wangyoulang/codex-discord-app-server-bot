from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


@dataclass(slots=True)
class TokenUsageBreakdown:
    total_tokens: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: object) -> "TokenUsageBreakdown":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            total_tokens=_coerce_int(payload.get("totalTokens")),
            input_tokens=_coerce_int(payload.get("inputTokens")),
            cached_input_tokens=_coerce_int(payload.get("cachedInputTokens")),
            output_tokens=_coerce_int(payload.get("outputTokens")),
            reasoning_output_tokens=_coerce_int(payload.get("reasoningOutputTokens")),
        )

    @classmethod
    def from_dict(cls, payload: object) -> "TokenUsageBreakdown":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            total_tokens=_coerce_int(payload.get("total_tokens")),
            input_tokens=_coerce_int(payload.get("input_tokens")),
            cached_input_tokens=_coerce_int(payload.get("cached_input_tokens")),
            output_tokens=_coerce_int(payload.get("output_tokens")),
            reasoning_output_tokens=_coerce_int(payload.get("reasoning_output_tokens")),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
        }


@dataclass(slots=True)
class TokenUsageSnapshot:
    thread_id: str
    turn_id: str
    total: TokenUsageBreakdown
    last: TokenUsageBreakdown
    model_context_window: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TokenUsageSnapshot | None":
        thread_id = payload.get("threadId")
        turn_id = payload.get("turnId")
        token_usage = payload.get("tokenUsage")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        if not isinstance(turn_id, str) or not turn_id:
            return None
        if not isinstance(token_usage, dict):
            return None

        raw_window = token_usage.get("modelContextWindow")
        model_context_window = _coerce_int(raw_window) if raw_window is not None else None
        return cls(
            thread_id=thread_id,
            turn_id=turn_id,
            total=TokenUsageBreakdown.from_payload(token_usage.get("total")),
            last=TokenUsageBreakdown.from_payload(token_usage.get("last")),
            model_context_window=model_context_window,
        )

    @classmethod
    def from_dict(cls, payload: object) -> "TokenUsageSnapshot | None":
        if not isinstance(payload, dict):
            return None
        thread_id = payload.get("thread_id")
        turn_id = payload.get("turn_id")
        if not isinstance(thread_id, str) or not thread_id:
            return None
        if not isinstance(turn_id, str) or not turn_id:
            return None
        raw_window = payload.get("model_context_window")
        model_context_window = _coerce_int(raw_window) if raw_window is not None else None
        return cls(
            thread_id=thread_id,
            turn_id=turn_id,
            total=TokenUsageBreakdown.from_dict(payload.get("total")),
            last=TokenUsageBreakdown.from_dict(payload.get("last")),
            model_context_window=model_context_window,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "total": self.total.to_dict(),
            "last": self.last.to_dict(),
            "model_context_window": self.model_context_window,
        }

    @property
    def context_used_tokens(self) -> int:
        return self.last.total_tokens

    @property
    def context_ratio(self) -> float | None:
        if self.model_context_window is None or self.model_context_window <= 0:
            return None
        return self.context_used_tokens / self.model_context_window

    @property
    def remaining_context_tokens(self) -> int | None:
        if self.model_context_window is None:
            return None
        return max(0, self.model_context_window - self.context_used_tokens)
