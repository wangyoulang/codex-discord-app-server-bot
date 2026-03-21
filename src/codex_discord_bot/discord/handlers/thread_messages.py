from __future__ import annotations

import asyncio

import discord

from codex_discord_bot.codex.approvals import ApprovalEnvelope
from codex_discord_bot.discord.views.approvals import ApprovalDecisionView
from codex_discord_bot.discord.views.session_controls import SessionControlView
from codex_discord_bot.logging import get_logger
from codex_discord_bot.utils.text import truncate_text

logger = get_logger(__name__)


async def handle_thread_message(bot: "CodexDiscordBot", message: discord.Message) -> None:
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.Thread):
        return
    if message.guild is None:
        return

    try:
        route = await bot.app_state.session_router.ensure_route_for_thread(message.channel)
    except ValueError:
        return

    worker_key = str(message.channel.id)
    if bot.app_state.worker_pool.is_busy(worker_key):
        worker = bot.app_state.worker_pool.get_worker(worker_key)
        if worker is None or worker.get_active_turn() is None:
            await message.reply("当前 turn 正在启动，请稍后再次发送消息。", mention_author=False)
            return

        try:
            turn_id = await worker.steer_text_turn(message.content)
        except Exception as exc:
            logger.exception("thread.message.steer_failed", error=str(exc), thread_id=message.channel.id)
            await message.reply(
                "追加到当前进行中的 Codex turn 失败，可能该 turn 已刚结束，请重新发送一条消息。",
                mention_author=False,
            )
            return

        await bot.app_state.session_service.mark_running(
            discord_thread_id=str(message.channel.id),
            active_turn_id=turn_id,
        )
        await bot.app_state.audit_service.record(
            action="thread_message_steered",
            guild_id=str(message.guild.id),
            discord_thread_id=str(message.channel.id),
            actor_id=str(message.author.id),
            payload={"content_length": len(message.content), "turn_id": turn_id},
        )
        await message.reply(
            f"已追加到当前进行中的 Codex turn：`{turn_id}`",
            mention_author=False,
        )
        return

    status_message = await message.reply(
        "正在调用 Codex...",
        mention_author=False,
        view=SessionControlView(bot.app_state),
    )
    edit_lock = asyncio.Lock()
    streamed_text = ""

    async def on_delta(delta: str) -> None:
        nonlocal streamed_text
        streamed_text += delta
        async with edit_lock:
            await status_message.edit(content=truncate_text(streamed_text))

    async def on_turn_started(codex_thread_id: str, turn_id: str) -> None:
        await bot.app_state.session_service.bind_codex_thread(
            discord_thread_id=str(message.channel.id),
            codex_thread_id=codex_thread_id,
        )
        await bot.app_state.session_service.mark_running(
            discord_thread_id=str(message.channel.id),
            active_turn_id=turn_id,
            last_bot_message_id=str(status_message.id),
        )

    async def on_approval_request(envelope: ApprovalEnvelope) -> dict:
        pending = await bot.app_state.approval_service.register_request(
            local_request_id=envelope.local_request_id,
            request_type=envelope.request_type,
            title=envelope.title,
            body=envelope.body,
            decisions=envelope.decisions,
            response_payloads=envelope.response_payloads,
            requester_id=str(message.author.id),
            thread_id=str(message.channel.id),
            turn_id=envelope.turn_id,
            item_id=envelope.item_id,
        )
        approval_message = await message.channel.send(
            f"**{pending.title}**\n{pending.body}",
            view=ApprovalDecisionView(
                bot.app_state,
                local_request_id=pending.local_request_id,
                decisions=pending.decisions,
            ),
        )
        await bot.app_state.approval_service.set_message_id(
            pending.local_request_id,
            str(approval_message.id),
        )

        try:
            result = await bot.app_state.approval_service.wait_for_decision(
                pending.local_request_id,
                timeout_seconds=900,
            )
            await bot.app_state.audit_service.record(
                action="approval_resolved",
                guild_id=str(message.guild.id),
                discord_thread_id=str(message.channel.id),
                actor_id=result.get("actor_id"),
                payload={
                    "local_request_id": pending.local_request_id,
                    "decision": result.get("decision"),
                },
            )
            response = result.get("response")
            if not isinstance(response, dict):
                return {"decision": "decline"}
            return response
        except TimeoutError:
            await approval_message.edit(
                content=f"{approval_message.content}\n\n审批超时，已自动取消。",
                view=None,
            )
            return {"decision": "cancel"}
        finally:
            await bot.app_state.approval_service.cleanup_request(pending.local_request_id)

    await bot.app_state.audit_service.record(
        action="thread_message_received",
        guild_id=str(message.guild.id),
        discord_thread_id=str(message.channel.id),
        actor_id=str(message.author.id),
        payload={"content_length": len(message.content)},
    )

    await bot.app_state.session_service.mark_running(
        discord_thread_id=str(message.channel.id),
        last_bot_message_id=str(status_message.id),
    )

    try:
        async with bot.app_state.worker_pool.lease(worker_key) as worker:
            codex_thread_id, _turn_id, reply_text = await worker.run_streamed_text_turn(
                route.session,
                route.workspace,
                message.content,
                on_delta=on_delta,
                on_turn_started=on_turn_started,
                on_approval_request=on_approval_request,
            )

        await bot.app_state.session_service.bind_codex_thread(
            discord_thread_id=str(message.channel.id),
            codex_thread_id=codex_thread_id,
        )
        await bot.app_state.session_service.mark_ready(
            discord_thread_id=str(message.channel.id),
            last_bot_message_id=str(status_message.id),
        )
        await status_message.edit(content=truncate_text(reply_text))
    except Exception as exc:
        logger.exception("thread.message.failed", error=str(exc), thread_id=message.channel.id)
        await bot.app_state.session_service.mark_error(
            discord_thread_id=str(message.channel.id),
            last_bot_message_id=str(status_message.id),
        )
        await status_message.edit(content=f"Codex 执行失败：{exc}")
