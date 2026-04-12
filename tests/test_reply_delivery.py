from __future__ import annotations

import asyncio
from pathlib import Path

from codex_discord_bot.discord.streaming.reply_delivery import send_local_image
from codex_discord_bot.discord.streaming.reply_delivery import send_text_pages


class FakeMessage:
    def __init__(self, channel: 'FakeThread', message_id: int, content: str | None) -> None:
        self.channel = channel
        self.id = message_id
        self.content = content
        self.reference = None
        self.file = None


class FakeThread:
    def __init__(self) -> None:
        self._next_id = 1000
        self.sent_messages: list[FakeMessage] = []

    async def send(
        self,
        content: str | None = None,
        *,
        reference=None,
        mention_author=False,
        file=None,
        view=None,
    ):  # noqa: ANN001
        del mention_author, view
        message = FakeMessage(self, self._next_id, content)
        message.reference = reference
        message.file = file
        self._next_id += 1
        self.sent_messages.append(message)
        return message


def test_send_text_pages_respects_overall_start_index() -> None:
    async def scenario() -> None:
        channel = FakeThread()
        reply_to_message = FakeMessage(channel, 99, '原消息')

        messages = await send_text_pages(
            channel=channel,  # type: ignore[arg-type]
            text='12345',
            reply_to_message=reply_to_message,  # type: ignore[arg-type]
            reply_to_mode='first',
            max_chars=4,
            max_lines=10,
            start_index=1,
        )

        assert len(messages) == 2
        assert [message.reference for message in messages] == [None, None]

    asyncio.run(scenario())


def test_send_local_image_replies_only_for_first_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        image_path = tmp_path / 'screen.png'
        image_path.write_bytes(b'png')
        channel = FakeThread()
        reply_to_message = FakeMessage(channel, 99, '原消息')

        first_message = await send_local_image(
            channel=channel,  # type: ignore[arg-type]
            image_path=image_path,
            reply_to_message=reply_to_message,  # type: ignore[arg-type]
            reply_to_mode='first',
            reply_index=0,
        )
        second_message = await send_local_image(
            channel=channel,  # type: ignore[arg-type]
            image_path=image_path,
            reply_to_message=reply_to_message,  # type: ignore[arg-type]
            reply_to_mode='first',
            reply_index=1,
        )

        assert first_message.reference is reply_to_message
        assert second_message.reference is None
        assert first_message.file.filename == 'screen.png'

    asyncio.run(scenario())



def test_send_local_image_supports_caption_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        image_path = tmp_path / 'screen.png'
        image_path.write_bytes(b'png')
        channel = FakeThread()
        reply_to_message = FakeMessage(channel, 99, '原消息')

        message = await send_local_image(
            channel=channel,  # type: ignore[arg-type]
            image_path=image_path,
            reply_to_message=reply_to_message,  # type: ignore[arg-type]
            reply_to_mode='first',
            reply_index=0,
            content='这是一张截图',
        )

        assert message.content == '这是一张截图'
        assert message.reference is reply_to_message
        assert message.file.filename == 'screen.png'

    asyncio.run(scenario())
