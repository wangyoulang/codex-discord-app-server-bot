from __future__ import annotations

import discord

from codex_discord_bot.logging import get_logger
from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.utils.text import truncate_text

logger = get_logger(__name__)


async def handle_thread_message(bot: "CodexDiscordBot", message: discord.Message) -> None:
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.Thread):
        return
    if message.guild is None:
        return

    worker_key = str(message.channel.id)
    if bot.app_state.worker_pool.is_busy(worker_key):
        await message.reply(
            "当前会话已有进行中的请求。第一版骨架暂未启用 `turn/steer`，请等待上一条完成。",
            mention_author=False,
        )
        return

    try:
        route = await bot.app_state.session_router.ensure_route_for_thread(message.channel)
    except ValueError:
        return

    status_message = await message.reply("正在调用 Codex...", mention_author=False)

    await bot.app_state.audit_service.record(
        action="thread_message_received",
        guild_id=str(message.guild.id),
        discord_thread_id=str(message.channel.id),
        actor_id=str(message.author.id),
        payload={"content_length": len(message.content)},
    )

    await bot.app_state.session_service.update_status(
        discord_thread_id=str(message.channel.id),
        status=SessionStatus.running,
        last_bot_message_id=str(status_message.id),
    )

    try:
        async with bot.app_state.worker_pool.lease(worker_key) as worker:
            codex_thread_id, reply_text = await worker.run_text_turn(
                route.session,
                route.workspace,
                message.content,
            )

        await bot.app_state.session_service.bind_codex_thread(
            discord_thread_id=str(message.channel.id),
            codex_thread_id=codex_thread_id,
        )
        await bot.app_state.session_service.update_status(
            discord_thread_id=str(message.channel.id),
            status=SessionStatus.ready,
            last_bot_message_id=str(status_message.id),
        )
        await status_message.edit(content=truncate_text(reply_text))
    except Exception as exc:
        logger.exception("thread.message.failed", error=str(exc), thread_id=message.channel.id)
        await bot.app_state.session_service.update_status(
            discord_thread_id=str(message.channel.id),
            status=SessionStatus.error,
            last_bot_message_id=str(status_message.id),
        )
        await status_message.edit(content=f"Codex 执行失败：{exc}")
