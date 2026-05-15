from __future__ import annotations


_MODEL_AT_CAPACITY_MARKERS: tuple[str, ...] = (
    "Selected model is at capacity",
)


def is_model_at_capacity_error(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    lowered = value.lower()
    return any(marker.lower() in lowered for marker in _MODEL_AT_CAPACITY_MARKERS)


def build_model_at_capacity_user_message(error_text: str) -> str:
    normalized = error_text.strip() if isinstance(error_text, str) else ""
    lines = [
        "模型容量已满，请切换到其他模型后重试。",
        "操作：执行 `/codex model set` 设置当前线程的 model 覆盖。",
    ]
    if normalized:
        lines.append(f"原始错误：{normalized}")
    return "\n".join(lines)

