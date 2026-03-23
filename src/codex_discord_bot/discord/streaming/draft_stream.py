from __future__ import annotations

import asyncio
import time
from typing import Awaitable
from typing import Callable

import discord

from codex_discord_bot.logging import get_logger

logger = get_logger(__name__)


MessageCreatedCallback = Callable[[discord.Message], Awaitable[None] | None]


class DiscordDraftStream:
    def __init__(
        self,
        *,
        channel: discord.abc.Messageable,
        max_chars: int,
        throttle_ms: int,
        min_initial_chars: int,
        on_message_created: MessageCreatedCallback | None = None,
    ) -> None:
        self.channel = channel
        self.max_chars = min(max_chars, 2000)
        self.throttle_ms = max(throttle_ms, 250)
        self.min_initial_chars = max(min_initial_chars, 0)
        self.on_message_created = on_message_created

        self._lock = asyncio.Lock()
        self._pending_text = ""
        self._last_sent_text = ""
        self._messages: list[discord.Message] = []
        self._current_message: discord.Message | None = None
        self._last_flush_at = 0.0
        self._stopped = False
        self._final = False

    @property
    def messages(self) -> list[discord.Message]:
        return list(self._messages)

    @property
    def current_message(self) -> discord.Message | None:
        return self._current_message

    async def update(self, text: str) -> None:
        async with self._lock:
            if self._stopped and not self._final:
                return

            trimmed = text.rstrip()
            if not trimmed:
                return
            if len(trimmed) > self.max_chars:
                self._stopped = True
                logger.info(
                    "discord.preview.stopped",
                    reason="text_too_long",
                    text_length=len(trimmed),
                    max_chars=self.max_chars,
                )
                return
            if self._current_message is None and len(trimmed) < self.min_initial_chars and not self._final:
                return

            self._pending_text = trimmed
            elapsed_ms = (time.monotonic() - self._last_flush_at) * 1000
            should_flush = self._current_message is None or elapsed_ms >= self.throttle_ms
            if should_flush:
                await self._flush_locked()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def stop(self) -> None:
        async with self._lock:
            self._final = True
            await self._flush_locked()

    async def clear(self) -> None:
        async with self._lock:
            for message in list(self._messages):
                try:
                    await message.delete()
                except discord.HTTPException:
                    logger.info("discord.preview.delete_failed", message_id=message.id)
            self._messages = []
            self._current_message = None
            self._pending_text = ""
            self._last_sent_text = ""
            self._last_flush_at = 0.0

    def force_new_message(self) -> None:
        self._current_message = None
        self._last_sent_text = ""

    async def _flush_locked(self) -> None:
        if not self._pending_text:
            return
        if self._pending_text == self._last_sent_text:
            return

        if self._current_message is None:
            message = await self.channel.send(self._pending_text)
            self._current_message = message
            self._messages.append(message)
            if self.on_message_created is not None:
                created = self.on_message_created(message)
                if created is not None:
                    await created
        else:
            await self._current_message.edit(content=self._pending_text)

        self._last_sent_text = self._pending_text
        self._last_flush_at = time.monotonic()
