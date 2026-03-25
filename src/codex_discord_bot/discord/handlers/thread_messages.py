from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from uuid import uuid4

import discord

from codex_discord_bot.codex.approvals import ApprovalEnvelope
from codex_discord_bot.codex.stream_events import TurnStartedEvent
from codex_discord_bot.discord.handlers.attachments import build_message_input_items
from codex_discord_bot.discord.handlers.attachments import collect_supported_attachments
from codex_discord_bot.discord.streaming.turn_output_controller import TurnOutputController
from codex_discord_bot.discord.views.approvals import ApprovalDecisionView
from codex_discord_bot.discord.views.session_controls import SessionControlView
from codex_discord_bot.logging import get_logger
from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.providers.types import provider_display_name
from codex_discord_bot.providers.types import provider_root_command

logger = get_logger(__name__)

if TYPE_CHECKING:
    from codex_discord_bot.discord.bot import CodexDiscordBot


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

    if route.session is None:
        roots: list[str] = []
        if bot.app_state.settings.enable_codex_command:
            roots.append("`/codex session new`")
        if bot.app_state.settings.enable_claude_command:
            roots.append("`/claude session new`")
        hint = " 或 ".join(roots) if roots else "对应的 session 命令"
        await message.reply(f"当前线程尚未初始化会话，请先执行 {hint}。", mention_author=False)
        return

    provider = route.session.provider
    provider_label = provider_display_name(provider)
    provider_root = provider_root_command(provider)
    worker_key = str(message.channel.id)
    is_busy = bot.app_state.worker_pool.is_busy(provider, worker_key)
    worker = bot.app_state.worker_pool.get_worker(provider, worker_key) if is_busy else None
    if route.session.codex_thread_id:
        try:
            await bot.app_state.codex_thread_service.ensure_thread_available_for_discord(
                workspace_id=route.workspace.id,
                codex_thread_id=route.session.codex_thread_id,
                discord_thread_id=str(message.channel.id),
                provider=provider,
            )
        except ValueError as exc:
            await message.reply(
                f"{exc}\n如需继续当前工作区中的其它会话，请执行 `/{provider_root} session resume`。",
                mention_author=False,
            )
            return

    if not is_busy and route.session.status.value == "running":
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
        "provider": provider.value,
        "message_id": str(message.id),
        "content_length": len(message.content),
        "attachment_count": attachment_count,
        "supported_image_count": supported_image_count,
    }

    if not input_items:
        await message.reply(
            f"当前消息里没有可发送给 {provider_label} 的文本或受支持图片附件。",
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
                f"已追加到当前进行中的 {provider_label} turn：`{turn_id}`",
                mention_author=False,
            )
            return
        except Exception as exc:
            error_text = str(exc)
            if "no active turn to steer" in error_text:
                logger.warning(
                    "thread.message.steer_stale_turn",
                    error=error_text,
                    thread_id=message.channel.id,
                    provider=provider.value,
                )
                await bot.app_state.worker_pool.force_reset(provider, worker_key)
                await bot.app_state.session_service.mark_ready(
                    discord_thread_id=str(message.channel.id),
                )
                is_busy = False
            elif "暂不支持运行中追加输入" in error_text:
                await message.reply(
                    f"当前 {provider_label} 回复尚未结束，暂不支持追加输入。请等待完成，或点击“打断”后再发送新消息。",
                    mention_author=False,
                )
                return
            else:
                logger.exception(
                    "thread.message.steer_failed",
                    error=error_text,
                    thread_id=message.channel.id,
                    provider=provider.value,
                )
                await message.reply(
                    f"追加到当前进行中的 {provider_label} turn 失败，可能该 turn 已刚结束，请重新发送一条消息。",
                    mention_author=False,
                )
                return

    control_message = await message.channel.send(
        f"正在调用 {provider_label}...",
        view=SessionControlView(bot.app_state),
    )
    controller = TurnOutputController(
        settings=bot.app_state.settings,
        turn_output_service=bot.app_state.turn_output_service,
        source_message=message,
        control_message=control_message,
        provider_label=provider_label,
        provider=provider,
    )

    async def on_event(event) -> None:
        if isinstance(event, TurnStartedEvent):
            await bot.app_state.session_service.bind_codex_thread(
                discord_thread_id=str(message.channel.id),
                codex_thread_id=event.thread_id,
                provider=provider,
            )
            await bot.app_state.codex_thread_service.ensure_thread_available_for_discord(
                workspace_id=route.workspace.id,
                codex_thread_id=event.thread_id,
                discord_thread_id=str(message.channel.id),
                provider=provider,
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

    async def on_approval_request(envelope) -> dict:
        approval_payload = _normalize_approval_envelope(envelope, provider_label=provider_label)
        pending = await bot.app_state.approval_service.register_request(
            local_request_id=approval_payload["local_request_id"],
            provider=provider,
            request_type=approval_payload["request_type"],
            title=approval_payload["title"],
            body=approval_payload["body"],
            decisions=approval_payload["decisions"],
            response_payloads=approval_payload["response_payloads"],
            requester_id=str(message.author.id),
            thread_id=str(message.channel.id),
            turn_id=approval_payload["turn_id"],
            item_id=approval_payload["item_id"],
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
                    "provider": provider.value,
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
        async with bot.app_state.worker_pool.lease(provider, worker_key) as worker:
            result = await worker.run_streamed_turn(
                route.session,
                route.workspace,
                input_items,
                on_event=on_event,
                on_approval_request=on_approval_request,
            )
            thread_payload = await worker.read_thread(result.thread_id, include_turns=False)

        if provider == ProviderKind.claude:
            thread_payload = {**thread_payload, "source": {"custom": "discord-bot"}}

        render_result = await controller.finalize(result)
        await bot.app_state.codex_thread_service.sync_thread_from_payload(
            workspace_id=route.workspace.id,
            thread_payload=thread_payload,
            archived=False,
            provider=provider,
        )
        await bot.app_state.session_service.bind_codex_thread(
            discord_thread_id=str(message.channel.id),
            codex_thread_id=result.thread_id,
            provider=provider,
        )
        if render_result.state.value == "failed":
            await bot.app_state.session_service.mark_error(
                discord_thread_id=str(message.channel.id),
                last_bot_message_id=render_result.last_message_id,
            )
        else:
            await bot.app_state.session_service.mark_ready(
                discord_thread_id=str(message.channel.id),
                last_bot_message_id=render_result.last_message_id,
            )
    except Exception as exc:
        logger.exception(
            "thread.message.failed",
            error=str(exc),
            thread_id=message.channel.id,
            provider=provider.value,
        )
        render_result = await controller.fail(str(exc))
        await bot.app_state.session_service.mark_error(
            discord_thread_id=str(message.channel.id),
            last_bot_message_id=render_result.last_message_id,
        )


def _normalize_approval_envelope(envelope: object, *, provider_label: str) -> dict[str, Any]:
    if isinstance(envelope, ApprovalEnvelope):
        return {
            "local_request_id": envelope.local_request_id,
            "request_type": envelope.request_type,
            "title": envelope.title,
            "body": envelope.body,
            "decisions": envelope.decisions,
            "turn_id": envelope.turn_id,
            "item_id": envelope.item_id,
            "response_payloads": envelope.response_payloads,
        }

    if isinstance(envelope, dict):
        tool_name = envelope.get("tool_name")
        tool_input = envelope.get("input")
        turn_id = envelope.get("turn_id")
        return {
            "local_request_id": f"claude-{uuid4().hex}",
            "request_type": "permissions",
            "title": f"{provider_label} 工具审批",
            "body": "\n".join(
                [
                    f"工具：`{tool_name}`" if isinstance(tool_name, str) and tool_name else f"{provider_label} 请求调用工具。",
                    f"输入：```json\n{tool_input}\n```" if tool_input is not None else "",
                ]
            ).strip(),
            "decisions": ("accept", "decline", "cancel"),
            "turn_id": str(turn_id) if isinstance(turn_id, str) else None,
            "item_id": None,
            "response_payloads": {
                "accept": {"decision": "accept"},
                "decline": {"decision": "decline", "message": "已拒绝当前工具调用。"},
                "cancel": {"decision": "cancel", "message": "已取消当前工具调用。"},
            },
        }

    raise TypeError(f"不支持的审批包类型：{type(envelope)!r}")
