from __future__ import annotations

import asyncio
from pathlib import Path

from codex_discord_bot.discord.handlers.attachments import CollectedAttachment
from codex_discord_bot.discord.handlers.attachments import build_message_input_items
from codex_discord_bot.discord.handlers.attachments import collect_supported_attachments


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeAttachment:
    def __init__(
        self,
        *,
        attachment_id: int,
        filename: str,
        content_type: str | None,
        payload: bytes,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        self.id = attachment_id
        self.filename = filename
        self.content_type = content_type
        self.size = len(payload)
        self.width = width
        self.height = height
        self._payload = payload

    async def save(self, target: str | Path) -> None:
        Path(target).write_bytes(self._payload)


class FakeMessage:
    def __init__(self, *, message_id: int, channel_id: int, attachments: list[FakeAttachment]) -> None:
        self.id = message_id
        self.channel = FakeChannel(channel_id)
        self.attachments = attachments


def test_collect_supported_attachments_only_keeps_images(tmp_path: Path) -> None:
    async def scenario() -> None:
        message = FakeMessage(
            message_id=2001,
            channel_id=1001,
            attachments=[
                FakeAttachment(
                    attachment_id=1,
                    filename="note.txt",
                    content_type="text/plain",
                    payload=b"ignore",
                ),
                FakeAttachment(
                    attachment_id=2,
                    filename="image.png",
                    content_type="image/webp",
                    payload=b"image-bytes",
                    width=320,
                    height=240,
                ),
            ],
        )

        collected = await collect_supported_attachments(message, artifact_root=tmp_path)

        assert len(collected) == 1
        assert collected[0].filename == "image.png"
        assert collected[0].content_type == "image/webp"
        assert collected[0].local_path.is_absolute() is True
        assert collected[0].local_path.suffix == ".webp"
        assert collected[0].local_path.read_bytes() == b"image-bytes"
        assert collected[0].as_input_item() == {
            "type": "localImage",
            "path": str(collected[0].local_path),
        }

    asyncio.run(scenario())


def test_build_message_input_items_keeps_text_and_images(tmp_path: Path) -> None:
    attachment = CollectedAttachment(
        filename="screen.webp",
        content_type="image/webp",
        size=4,
        local_path=(tmp_path / "screen.webp").resolve(),
    )

    input_items = build_message_input_items(
        message_content="请帮我解析这张图",
        attachments=[attachment],
    )

    assert input_items == [
        {"type": "text", "text": "请帮我解析这张图"},
        {"type": "localImage", "path": str(attachment.local_path)},
    ]


def test_build_message_input_items_supports_pure_image_message(tmp_path: Path) -> None:
    attachment = CollectedAttachment(
        filename="screen.webp",
        content_type="image/webp",
        size=4,
        local_path=(tmp_path / "screen.webp").resolve(),
    )

    input_items = build_message_input_items(
        message_content="   ",
        attachments=[attachment],
    )

    assert input_items == [
        {"type": "localImage", "path": str(attachment.local_path)},
    ]
