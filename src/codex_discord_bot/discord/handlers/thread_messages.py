from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from codex_discord_bot.codex.approvals import ApprovalEnvelope
from codex_discord_bot.codex.stream_events import TurnStartedEvent
from codex_discord_bot.discord.handlers.attachments import build_message_input_items
from codex_discord_bot.discord.handlers.attachments import collect_supported_attachments
from codex_discord_bot.discord.streaming.delivery import DiscordDeliveryError
from codex_discord_bot.discord.streaming.turn_output_controller import TurnOutputController
from codex_discord_bot.discord.views.approvals import ApprovalDecisionView
from codex_discord_bot.discord.views.session_controls import SessionControlView
from codex_discord_bot.logging import get_logger
from codex_discord_bot.persistence.enums import SessionStatus

logger = get_logger(__name__)

if TYPE_CHECKING:
    from codex_discord_bot.discord.bot import CodexDiscordBot


def _session_is_initialized(session: object) -> bool:
    codex_thread_id = getattr(session, "codex_thread_id", None)
    status = getattr(session, "status", None)
    if not isinstance(codex_thread_id, str) or not codex_thread_id:
        return False
    return status != SessionStatus.uninitialized


def _is_missing_thread_error(exc: Exception) -> bool:
    message = str(exc)
    return "thread not loaded:" in message or "no rollout found for thread id" in message


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

    if not _session_is_initialized(route.session):
        await bot.app_state.audit_service.record(
            action="thread_message_blocked_uninitialized",
            guild_id=str(message.guild.id),
            discord_thread_id=str(message.channel.id),
            actor_id=str(message.author.id),
            payload={
                "message_id": str(message.id),
                "content_length": len(message.content),
                "attachment_count": len(message.attachments),
            },
        )
        await message.reply(
            "当前线程尚未初始化 Codex 会话，请先执行 `/codex session new` 创建新会话，"
            "或执行 `/codex session list` 后再用 `/codex session resume` 恢复历史会话。"
            "当前消息不会发送给 Codex。",
            mention_author=False,
        )
        return

    worker_key = str(message.channel.id)
    is_busy = bot.app_state.worker_pool.is_busy(worker_key)
    worker = bot.app_state.worker_pool.get_worker(worker_key) if is_busy else None
    if route.session.codex_thread_id:
        existing_codex_thread = await bot.app_state.codex_thread_service.get_by_codex_thread_id(
            route.session.codex_thread_id
        )
        if existing_codex_thread is not None:
            try:
                await bot.app_state.codex_thread_service.ensure_thread_available_for_discord(
                    workspace_id=route.workspace.id,
                    codex_thread_id=route.session.codex_thread_id,
                    discord_thread_id=str(message.channel.id),
                )
            except ValueError as exc:
                await message.reply(
                    f"{exc}\n如需继续当前工作区中的其他会话，请执行 `/codex session resume`。",
                    mention_author=False,
                )
                return

    if not is_busy and route.session.status == SessionStatus.running:
        await bot.app_state.session_service.mark_ready(
            discord_thread_id=str(message.channel.id),
        )

    if is_busy:
        if worker is None or worker.get_active_turn() is None:
            await message.reply("当前 turn 正在启动，请稍后再次发送消息。", mention_author=False)
            return

    try:
        attachments = await collect_supported_attachments(
            message,
            artifact_root=bot.app_state.artifact_service.artifact_root,
        )
    except Exception as exc:
        logger.exception(
            "thread.message.attachments_failed",
            error=str(exc),
            thread_id=message.channel.id,
            message_id=message.id,
        )
        await message.reply("读取当前消息里的图片附件失败，请稍后重试或重新上传。", mention_author=False)
        return

    input_items = build_message_input_items(
        message_content=message.content,
        attachments=attachments,
    )
    attachment_count = len(message.attachments)
    supported_image_count = len(attachments)
    audit_payload = {
        "message_id": str(message.id),
        "content_length": len(message.content),
        "attachment_count": attachment_count,
        "supported_image_count": supported_image_count,
    }

    if not input_items:
        await message.reply(
            "当前消息里没有可发送给 Codex 的文本或受支持图片附件。",
            mention_author=False,
        )
        return

    if is_busy:
        try:
            turn_id = await worker.steer_turn(input_items)
            await bot.app_state.session_service.mark_running(
                discord_thread_id=str(message.channel.id),
                active_turn_id=turn_id,
            )
            await bot.app_state.audit_service.record(
                action="thread_message_steered",
                guild_id=str(message.guild.id),
                discord_thread_id=str(message.channel.id),
                actor_id=str(message.author.id),
                payload={**audit_payload, "turn_id": turn_id},
            )
            await message.reply(
                f"已追加到当前进行中的 Codex turn：`{turn_id}`",
                mention_author=False,
            )
            return
        except Exception as exc:
            if "no active turn to steer" in str(exc):
                logger.warning(
                    "thread.message.steer_stale_turn",
                    error=str(exc),
                    thread_id=message.channel.id,
                )
                await bot.app_state.worker_pool.force_reset(worker_key)
                await bot.app_state.session_service.mark_ready(
                    discord_thread_id=str(message.channel.id),
                )
                is_busy = False
            else:
                logger.exception("thread.message.steer_failed", error=str(exc), thread_id=message.channel.id)
                await message.reply(
                    "追加到当前进行中的 Codex turn 失败，可能该 turn 已刚结束，请重新发送一条消息。",
                    mention_author=False,
                )
                return

    control_message = await message.channel.send(
        "正在调用 Codex...",
        view=SessionControlView(bot.app_state),
    )
    controller = TurnOutputController(
        settings=bot.app_state.settings,
        turn_output_service=bot.app_state.turn_output_service,
        source_message=message,
        control_message=control_message,
        workspace_cwd=route.workspace.cwd,
    )

    async def on_event(event) -> None:
        if isinstance(event, TurnStartedEvent):
            await bot.app_state.session_service.bind_codex_thread(
                discord_thread_id=str(message.channel.id),
                codex_thread_id=event.thread_id,
            )
            await bot.app_state.codex_thread_service.ensure_thread_available_for_discord(
                workspace_id=route.workspace.id,
                codex_thread_id=event.thread_id,
                discord_thread_id=str(message.channel.id),
            )
            await bot.app_state.session_service.mark_running(
                discord_thread_id=str(message.channel.id),
                active_turn_id=event.turn_id,
                last_bot_message_id=str(control_message.id),
            )
            await controller.bind_turn(
                codex_thread_id=event.thread_id,
                turn_id=event.turn_id,
            )
            return
        await controller.handle_event(event)

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
        payload=audit_payload,
    )

    await bot.app_state.session_service.mark_running(
        discord_thread_id=str(message.channel.id),
        last_bot_message_id=str(control_message.id),
    )

    try:
        async with bot.app_state.worker_pool.lease(worker_key) as worker:
            try:
                result = await worker.run_streamed_turn(
                    route.session,
                    route.workspace,
                    input_items,
                    on_event=on_event,
                    on_approval_request=on_approval_request,
                )
            except Exception as exc:
                if not route.session.codex_thread_id or not _is_missing_thread_error(exc):
                    raise

                stale_codex_thread_id = route.session.codex_thread_id
                logger.warning(
                    "thread.message.missing_thread_recovered",
                    error=str(exc),
                    thread_id=message.channel.id,
                    stale_codex_thread_id=stale_codex_thread_id,
                )
                await bot.app_state.codex_thread_service.release_binding_if_owned(
                    codex_thread_id=stale_codex_thread_id,
                    discord_thread_id=str(message.channel.id),
                )
                await bot.app_state.session_service.detach_codex_thread(
                    discord_thread_id=str(message.channel.id),
                )
                route.session.codex_thread_id = None
                route.session.status = SessionStatus.ready
                await bot.app_state.audit_service.record(
                    action="thread_message_recovered_missing_thread",
                    guild_id=str(message.guild.id),
                    discord_thread_id=str(message.channel.id),
                    actor_id=str(message.author.id),
                    payload={
                        **audit_payload,
                        "stale_codex_thread_id": stale_codex_thread_id,
                    },
                )
                result = await worker.run_streamed_turn(
                    route.session,
                    route.workspace,
                    input_items,
                    on_event=on_event,
                    on_approval_request=on_approval_request,
                )
            thread_payload = await worker.read_thread(result.thread_id, include_turns=False)

        render_result = await controller.finalize(result)
        await bot.app_state.codex_thread_service.sync_thread_from_payload(
            workspace_id=route.workspace.id,
            thread_payload=thread_payload,
            archived=False,
            source_override={"custom": "discord-bot"},
        )
        await bot.app_state.session_service.bind_codex_thread(
            discord_thread_id=str(message.channel.id),
            codex_thread_id=result.thread_id,
        )
        if getattr(render_result.state, "value", render_result.state) == "failed":
            await bot.app_state.session_service.mark_error(
                discord_thread_id=str(message.channel.id),
                last_bot_message_id=render_result.last_message_id,
            )
        else:
            await bot.app_state.session_service.mark_ready(
                discord_thread_id=str(message.channel.id),
                last_bot_message_id=render_result.last_message_id,
            )
    except DiscordDeliveryError as exc:
        logger.warning("thread.message.delivery_failed", error=str(exc), thread_id=message.channel.id)
        render_result = await controller.delivery_failed(str(exc))
        await bot.app_state.session_service.mark_ready(
            discord_thread_id=str(message.channel.id),
            last_bot_message_id=render_result.last_message_id,
        )
    except Exception as exc:
        logger.exception("thread.message.failed", error=str(exc), thread_id=message.channel.id)
        render_result = await controller.fail(str(exc))
        await bot.app_state.session_service.mark_error(
            discord_thread_id=str(message.channel.id),
            last_bot_message_id=render_result.last_message_id,
        )
