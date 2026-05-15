from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from codex_discord_bot.codex.errors import is_model_at_capacity_error
from codex_discord_bot.codex.approvals import ApprovalEnvelope
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
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


@dataclass(slots=True)
class CodexTurnTimeoutPolicy:
    hard_timeout_seconds: float = 0
    stall_timeout_seconds: float = 1800
    command_stall_timeout_seconds: float = 7200
    soft_warn_seconds: float = 1800


class CodexTurnTimeoutError(TimeoutError):
    def __init__(
        self,
        *,
        timeout_type: str,
        timeout_seconds: float,
        elapsed_seconds: float,
        idle_seconds: float,
        active_item_type: str | None,
    ) -> None:
        self.timeout_type = timeout_type
        self.timeout_seconds = timeout_seconds
        self.elapsed_seconds = elapsed_seconds
        self.idle_seconds = idle_seconds
        self.active_item_type = active_item_type
        if timeout_type == "hard":
            message = (
                f"Codex 执行超过硬上限 {timeout_seconds:g} 秒仍未结束，已重置当前 worker。"
                "可能原因是任务过大、命令执行过久或 app-server 未返回终止事件。"
            )
        else:
            message = (
                f"Codex 已连续 {timeout_seconds:g} 秒没有进展事件，已重置当前 worker。"
                "可能原因是 app-server 卡住、网络请求无响应或长命令无输出。"
            )
        super().__init__(message)


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


def _timeout_value(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_turn_timeout_policy(settings: object) -> CodexTurnTimeoutPolicy:
    legacy_stall_timeout = _timeout_value(
        getattr(settings, "codex_turn_timeout_seconds", 1800),
        1800,
    )
    return CodexTurnTimeoutPolicy(
        hard_timeout_seconds=_timeout_value(
            getattr(settings, "codex_turn_hard_timeout_seconds", 0),
            0,
        ),
        stall_timeout_seconds=_timeout_value(
            getattr(settings, "codex_turn_stall_timeout_seconds", legacy_stall_timeout),
            legacy_stall_timeout,
        ),
        command_stall_timeout_seconds=_timeout_value(
            getattr(settings, "codex_turn_command_stall_timeout_seconds", 7200),
            7200,
        ),
        soft_warn_seconds=_timeout_value(
            getattr(settings, "codex_turn_soft_warn_seconds", 1800),
            1800,
        ),
    )


def _active_stall_timeout(policy: CodexTurnTimeoutPolicy, active_item_type: str | None) -> float:
    if active_item_type == "commandExecution" and policy.command_stall_timeout_seconds > 0:
        return policy.command_stall_timeout_seconds
    return policy.stall_timeout_seconds


async def _run_codex_turn_with_timeout(
    worker: object,
    session: object,
    workspace: object,
    input_items: object,
    *,
    on_event: object,
    on_approval_request: object,
    timeout_policy: CodexTurnTimeoutPolicy,
    on_long_running: object | None = None,
) -> object:
    run_streamed_turn = getattr(worker, "run_streamed_turn")
    if (
        timeout_policy.hard_timeout_seconds <= 0
        and timeout_policy.stall_timeout_seconds <= 0
        and timeout_policy.command_stall_timeout_seconds <= 0
        and timeout_policy.soft_warn_seconds <= 0
    ):
        return await run_streamed_turn(
            session,
            workspace,
            input_items,
            on_event=on_event,
            on_approval_request=on_approval_request,
        )

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    last_progress_at = started_at
    active_item_id: str | None = None
    active_item_type: str | None = None
    soft_warned = False

    async def progress_on_event(event) -> None:
        nonlocal active_item_id, active_item_type, last_progress_at
        last_progress_at = loop.time()
        if isinstance(event, ItemStartedEvent):
            active_item_id = event.item_id
            active_item_type = event.item_type
        elif isinstance(event, ItemCompletedEvent) and event.item_id == active_item_id:
            active_item_id = None
            active_item_type = None
        await on_event(event)

    task = asyncio.create_task(
        run_streamed_turn(
            session,
            workspace,
            input_items,
            on_event=progress_on_event,
            on_approval_request=on_approval_request,
        )
    )
    while True:
        now = loop.time()
        deadlines: list[float] = []
        if timeout_policy.hard_timeout_seconds > 0:
            deadlines.append(started_at + timeout_policy.hard_timeout_seconds)
        stall_timeout = _active_stall_timeout(timeout_policy, active_item_type)
        if stall_timeout > 0:
            deadlines.append(last_progress_at + stall_timeout)
        if timeout_policy.soft_warn_seconds > 0 and not soft_warned:
            deadlines.append(started_at + timeout_policy.soft_warn_seconds)

        wait_seconds = 1.0
        if deadlines:
            wait_seconds = max(0, min(deadlines) - now)

        done, _pending = await asyncio.wait({task}, timeout=wait_seconds)
        if task in done:
            return await task

        now = loop.time()
        elapsed_seconds = now - started_at
        idle_seconds = now - last_progress_at
        if (
            timeout_policy.hard_timeout_seconds > 0
            and elapsed_seconds >= timeout_policy.hard_timeout_seconds
        ):
            task.cancel()
            raise CodexTurnTimeoutError(
                timeout_type="hard",
                timeout_seconds=timeout_policy.hard_timeout_seconds,
                elapsed_seconds=elapsed_seconds,
                idle_seconds=idle_seconds,
                active_item_type=active_item_type,
            )

        stall_timeout = _active_stall_timeout(timeout_policy, active_item_type)
        if stall_timeout > 0 and idle_seconds >= stall_timeout:
            task.cancel()
            raise CodexTurnTimeoutError(
                timeout_type="stall",
                timeout_seconds=stall_timeout,
                elapsed_seconds=elapsed_seconds,
                idle_seconds=idle_seconds,
                active_item_type=active_item_type,
            )

        if (
            timeout_policy.soft_warn_seconds > 0
            and not soft_warned
            and elapsed_seconds >= timeout_policy.soft_warn_seconds
        ):
            soft_warned = True
            if on_long_running is not None:
                await on_long_running(
                    elapsed_seconds=elapsed_seconds,
                    idle_seconds=idle_seconds,
                    active_item_type=active_item_type,
                )


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

    async def on_long_running(
        *,
        elapsed_seconds: float,
        idle_seconds: float,
        active_item_type: str | None,
    ) -> None:
        await controller.mark_long_running(
            elapsed_seconds=elapsed_seconds,
            idle_seconds=idle_seconds,
            active_item_type=active_item_type,
        )

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
    turn_timeout_policy = _build_turn_timeout_policy(bot.app_state.settings)

    try:
        async with bot.app_state.worker_pool.lease(worker_key) as worker:
            try:
                result = await _run_codex_turn_with_timeout(
                    worker,
                    route.session,
                    route.workspace,
                    input_items,
                    on_event=on_event,
                    on_approval_request=on_approval_request,
                    timeout_policy=turn_timeout_policy,
                    on_long_running=on_long_running,
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
                result = await _run_codex_turn_with_timeout(
                    worker,
                    route.session,
                    route.workspace,
                    input_items,
                    on_event=on_event,
                    on_approval_request=on_approval_request,
                    timeout_policy=turn_timeout_policy,
                    on_long_running=on_long_running,
                )
            thread_payload = await worker.read_thread(result.thread_id, include_turns=False)

        if is_model_at_capacity_error(getattr(result, "error_message", "") or ""):
            await bot.app_state.audit_service.record(
                action="codex_model_at_capacity",
                guild_id=str(message.guild.id),
                discord_thread_id=str(message.channel.id),
                actor_id=str(message.author.id),
                payload={
                    **audit_payload,
                    "codex_thread_id": getattr(result, "thread_id", None),
                    "turn_id": getattr(result, "turn_id", None),
                    "error_message": getattr(result, "error_message", None),
                    "model_override": getattr(route.session, "model_override", None),
                },
            )

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
    except CodexTurnTimeoutError as exc:
        logger.warning(
            "thread.message.turn_timeout",
            timeout_type=exc.timeout_type,
            timeout_seconds=exc.timeout_seconds,
            elapsed_seconds=round(exc.elapsed_seconds, 3),
            idle_seconds=round(exc.idle_seconds, 3),
            active_item_type=exc.active_item_type,
            thread_id=message.channel.id,
            codex_thread_id=route.session.codex_thread_id,
            turn_id=getattr(controller, "turn_id", None),
        )
        await bot.app_state.worker_pool.force_reset(worker_key)
        await bot.app_state.audit_service.record(
            action="thread_message_turn_timeout",
            guild_id=str(message.guild.id),
            discord_thread_id=str(message.channel.id),
            actor_id=str(message.author.id),
            payload={
                **audit_payload,
                "codex_thread_id": route.session.codex_thread_id,
                "turn_id": getattr(controller, "turn_id", None),
                "timeout_type": exc.timeout_type,
                "timeout_seconds": exc.timeout_seconds,
                "elapsed_seconds": round(exc.elapsed_seconds, 3),
                "idle_seconds": round(exc.idle_seconds, 3),
                "active_item_type": exc.active_item_type,
            },
        )
        render_result = await controller.fail(str(exc))
        await bot.app_state.session_service.mark_error(
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
        if is_model_at_capacity_error(str(exc)):
            await bot.app_state.audit_service.record(
                action="codex_model_at_capacity",
                guild_id=str(message.guild.id),
                discord_thread_id=str(message.channel.id),
                actor_id=str(message.author.id),
                payload={
                    **audit_payload,
                    "codex_thread_id": route.session.codex_thread_id,
                    "turn_id": getattr(controller, "turn_id", None),
                    "error_message": str(exc),
                    "model_override": getattr(route.session, "model_override", None),
                },
            )
        render_result = await controller.fail(str(exc))
        await bot.app_state.session_service.mark_error(
            discord_thread_id=str(message.channel.id),
            last_bot_message_id=render_result.last_message_id,
        )
