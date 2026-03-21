from __future__ import annotations

from typing import Iterable


def assistant_text_from_items(items: Iterable[object] | None) -> str:
    if items is None:
        return ""

    chunks: list[str] = []
    for item in items:
        raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw_item, dict):
            continue

        item_type = raw_item.get("type")
        if item_type == "agentMessage":
            text = raw_item.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
            continue

        if item_type != "message" or raw_item.get("role") != "assistant":
            continue

        for content in raw_item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)

    return "".join(chunks)
