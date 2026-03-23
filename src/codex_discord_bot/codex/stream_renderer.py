from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class AssistantMessageSnapshot:
    item_id: str
    text: str


def assistant_messages_from_items(items: Iterable[object] | None) -> list[AssistantMessageSnapshot]:
    if items is None:
        return []

    snapshots: list[AssistantMessageSnapshot] = []
    for index, item in enumerate(items):
        raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw_item, dict):
            continue

        item_type = raw_item.get("type")
        if item_type == "agentMessage":
            text = raw_item.get("text")
            if isinstance(text, str) and text:
                item_id = raw_item.get("id")
                snapshots.append(
                    AssistantMessageSnapshot(
                        item_id=item_id if isinstance(item_id, str) and item_id else f"agentMessage:{index}",
                        text=text,
                    )
                )
            continue

        if item_type != "message" or raw_item.get("role") != "assistant":
            continue

        chunks: list[str] = []
        for content in raw_item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)

        if chunks:
            item_id = raw_item.get("id")
            snapshots.append(
                AssistantMessageSnapshot(
                    item_id=item_id if isinstance(item_id, str) and item_id else f"message:{index}",
                    text="".join(chunks),
                )
            )

    return snapshots


def assistant_text_from_items(items: Iterable[object] | None) -> str:
    return "".join(snapshot.text for snapshot in assistant_messages_from_items(items))
