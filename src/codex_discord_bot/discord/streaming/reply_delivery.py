from __future__ import annotations

from pathlib import Path

import discord

from codex_discord_bot.discord.streaming.chunker import ChunkMode
from codex_discord_bot.discord.streaming.chunker import chunk_discord_text


async def delete_messages(messages: list[discord.Message]) -> None:
    for message in messages:
        try:
            await message.delete()
        except discord.HTTPException:
            continue


async def send_text_pages(
    *,
    channel: discord.Thread,
    text: str,
    reply_to_message: discord.Message | None,
    reply_to_mode: str,
    max_chars: int,
    max_lines: int,
    chunk_mode: ChunkMode = "length",
    start_index: int = 0,
) -> list[discord.Message]:
    pages = chunk_discord_text(
        text,
        max_chars=max_chars,
        max_lines=max_lines,
        chunk_mode=chunk_mode,
    )
    if not pages:
        pages = [text]
    return await send_text_chunks(
        channel=channel,
        chunks=pages,
        reply_to_message=reply_to_message,
        reply_to_mode=reply_to_mode,
        start_index=start_index,
    )


async def send_text_chunks(
    *,
    channel: discord.Thread,
    chunks: list[str],
    reply_to_message: discord.Message | None,
    reply_to_mode: str,
    start_index: int = 0,
) -> list[discord.Message]:
    sent_messages: list[discord.Message] = []
    for index, chunk in enumerate(chunks):
        chunk_index = start_index + index
        sent = await channel.send(
            content=chunk,
            reference=reply_to_message if _should_reply(reply_to_message, reply_to_mode, chunk_index) else None,
            mention_author=False,
        )
        sent_messages.append(sent)
    return sent_messages


async def send_local_image(
    *,
    channel: discord.Thread,
    image_path: Path,
    reply_to_message: discord.Message | None,
    reply_to_mode: str,
    reply_index: int = 0,
    content: str | None = None,
) -> discord.Message:
    return await channel.send(
        content=content,
        file=discord.File(str(image_path), filename=image_path.name),
        reference=reply_to_message if _should_reply(reply_to_message, reply_to_mode, reply_index) else None,
        mention_author=False,
    )


def _should_reply(
    reply_to_message: discord.Message | None,
    reply_to_mode: str,
    reply_index: int,
) -> bool:
    if reply_to_message is None:
        return False
    if reply_to_mode == "all":
        return True
    return reply_to_mode == "first" and reply_index == 0
