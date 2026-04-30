from __future__ import annotations

from codex_discord_bot.codex.token_usage import TokenUsageSnapshot


def format_token_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_context_usage_summary_lines(snapshot: TokenUsageSnapshot | None) -> list[str]:
    if snapshot is None:
        return []

    used = snapshot.context_used_tokens
    total = snapshot.total.total_tokens
    if snapshot.model_context_window is None or snapshot.model_context_window <= 0:
        return [
            f"上下文：{format_token_count(used)} / 未知",
            f"累计：{format_token_count(total)} tokens",
        ]

    ratio = min(max(snapshot.context_ratio or 0.0, 0.0), 9.99)
    percent = ratio * 100
    return [
        "上下文："
        f"{format_token_count(used)} / {format_token_count(snapshot.model_context_window)}"
        f"（{percent:.0f}%） {_build_usage_bar(percent)}",
        f"累计：{format_token_count(total)} tokens",
    ]


def format_context_usage_detail_lines(payload: object) -> list[str]:
    snapshot = TokenUsageSnapshot.from_dict(payload)
    if snapshot is None:
        return ["context_usage: `无`"]

    ratio = snapshot.context_ratio
    ratio_label = "未知" if ratio is None else f"{ratio * 100:.1f}%"
    window_label = snapshot.model_context_window if snapshot.model_context_window is not None else "未知"
    remaining_label = (
        snapshot.remaining_context_tokens if snapshot.remaining_context_tokens is not None else "未知"
    )
    return [
        f"context_used: `{snapshot.context_used_tokens}`",
        f"context_window: `{window_label}`",
        f"context_ratio: `{ratio_label}`",
        f"remaining_context: `{remaining_label}`",
        f"last_input_tokens: `{snapshot.last.input_tokens}`",
        f"last_cached_input_tokens: `{snapshot.last.cached_input_tokens}`",
        f"last_output_tokens: `{snapshot.last.output_tokens}`",
        f"last_reasoning_output_tokens: `{snapshot.last.reasoning_output_tokens}`",
        f"cumulative_total_tokens: `{snapshot.total.total_tokens}`",
    ]


def _build_usage_bar(percent: float) -> str:
    filled = round(min(max(percent, 0.0), 100.0) / 10)
    return f"[{'█' * filled}{'░' * (10 - filled)}]"
