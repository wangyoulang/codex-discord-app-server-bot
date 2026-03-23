from __future__ import annotations

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
) -> list[discord.Message]:
    pages = chunk_discord_text(
        text,
        max_chars=max_chars,
        max_lines=max_lines,
        chunk_mode=chunk_mode,
    )
    if not pages:
        pages = [text]

    sent_messages: list[discord.Message] = []
    for index, page in enumerate(pages):
        should_reply = reply_to_message is not None and (
            reply_to_mode == "all" or (reply_to_mode == "first" and index == 0)
        )
        sent = await channel.send(
            content=page,
            reference=reply_to_message if should_reply else None,
            mention_author=False,
        )
        sent_messages.append(sent)
    return sent_messages
