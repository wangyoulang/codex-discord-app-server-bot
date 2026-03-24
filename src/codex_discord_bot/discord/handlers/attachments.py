from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePath

import discord


SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
SUPPORTED_IMAGE_SUFFIXES = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}
CONTENT_TYPE_TO_SUFFIX = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(slots=True)
class CollectedAttachment:
    filename: str
    content_type: str | None
    size: int
    local_path: Path

    def as_input_item(self) -> dict[str, str]:
        return {"type": "localImage", "path": str(self.local_path)}


def build_message_input_items(
    *,
    message_content: str,
    attachments: list[CollectedAttachment],
) -> list[dict[str, str]]:
    input_items: list[dict[str, str]] = []
    if message_content.strip():
        input_items.append({"type": "text", "text": message_content})
    input_items.extend(attachment.as_input_item() for attachment in attachments)
    return input_items


async def collect_supported_attachments(
    message: discord.Message,
    *,
    artifact_root: Path,
) -> list[CollectedAttachment]:
    collected: list[CollectedAttachment] = []
    if not message.attachments:
        return collected

    message_dir: Path | None = None
    for index, attachment in enumerate(message.attachments, start=1):
        if not _is_supported_image_attachment(attachment):
            continue

        if message_dir is None:
            message_dir = artifact_root / "discord-inputs" / str(message.channel.id) / str(message.id)
            message_dir.mkdir(parents=True, exist_ok=True)

        target_path = message_dir / f"{index:02d}-{attachment.id}{_guess_attachment_suffix(attachment)}"
        await attachment.save(target_path)
        collected.append(
            CollectedAttachment(
                filename=attachment.filename,
                content_type=attachment.content_type,
                size=attachment.size,
                local_path=target_path.resolve(),
            )
        )
    return collected


def _is_supported_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    suffix = PurePath(attachment.filename).suffix.lower()
    if content_type in SUPPORTED_IMAGE_CONTENT_TYPES:
        return True
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return True
    return attachment.width is not None or attachment.height is not None


def _guess_attachment_suffix(attachment: discord.Attachment) -> str:
    content_type = (attachment.content_type or "").lower()
    if content_type in CONTENT_TYPE_TO_SUFFIX:
        return CONTENT_TYPE_TO_SUFFIX[content_type]

    suffix = PurePath(attachment.filename).suffix.lower()
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return suffix
    return ".img"
