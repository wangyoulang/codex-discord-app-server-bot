from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class AssistantMessageSnapshot:
    item_id: str
    text: str


@dataclass(slots=True)
class OutputImageArtifact:
    item_id: str
    path: str
    source_type: str
    parent_item_id: str | None = None


SUPPORTED_OUTPUT_IMAGE_SUFFIXES = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}


def assistant_messages_from_items(items: Iterable[object] | None) -> list[AssistantMessageSnapshot]:
    if items is None:
        return []

    snapshots: list[AssistantMessageSnapshot] = []
    for index, item in enumerate(items):
        raw_item = _normalize_item(item)
        if raw_item is None:
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


def output_images_from_items(items: Iterable[object] | None) -> list[OutputImageArtifact]:
    if items is None:
        return []

    artifacts: list[OutputImageArtifact] = []
    for index, item in enumerate(items):
        raw_item = _normalize_item(item)
        if raw_item is None:
            continue

        item_type = raw_item.get("type")
        item_id = raw_item.get("id")
        normalized_item_id = item_id if isinstance(item_id, str) and item_id else f"image:{index}"

        if item_type == "imageView":
            image_path = _normalize_image_path(raw_item.get("path"))
            if image_path is None:
                continue
            artifacts.append(
                OutputImageArtifact(
                    item_id=normalized_item_id,
                    path=image_path,
                    source_type="imageView",
                )
            )
            continue

        if item_type != "imageGeneration":
            continue
        if raw_item.get("status") != "completed":
            continue

        image_path = _normalize_image_path(raw_item.get("savedPath"))
        if image_path is None:
            continue
        artifacts.append(
            OutputImageArtifact(
                item_id=normalized_item_id,
                path=image_path,
                source_type="imageGeneration",
            )
        )

    return artifacts


def assistant_text_from_items(items: Iterable[object] | None) -> str:
    return "".join(snapshot.text for snapshot in assistant_messages_from_items(items))


def _normalize_item(item: object) -> dict | None:
    raw_item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return raw_item if isinstance(raw_item, dict) else None


def _normalize_image_path(raw_value: object) -> str | None:
    if not isinstance(raw_value, str) or not raw_value:
        return None

    path = Path(raw_value)
    if not path.is_absolute():
        return None
    if path.suffix.lower() not in SUPPORTED_OUTPUT_IMAGE_SUFFIXES:
        return None
    return str(path.resolve(strict=False))
