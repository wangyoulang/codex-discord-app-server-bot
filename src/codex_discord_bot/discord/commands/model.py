from __future__ import annotations

import discord
from discord import app_commands

from codex_discord_bot.discord.handlers.interactions import send_interaction_error
from codex_discord_bot.discord.handlers.interactions import send_interaction_message


def build_group(app_state) -> app_commands.Group:
    group = app_commands.Group(name="model", description="模型切换与查看")

    def _normalize_model(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    async def _ensure_thread_session(interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            raise ValueError("请在论坛线程中执行该命令。")
        return await app_state.session_router.ensure_route_for_thread(interaction.channel)

    @group.command(name="status", description="查看当前 Discord 线程的 model 设置")
    async def status(interaction: discord.Interaction) -> None:
        try:
            route = await _ensure_thread_session(interaction)
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return

        override = _normalize_model(getattr(route.session, "model_override", None))
        if override is None:
            await send_interaction_message(
                interaction,
                "当前线程未设置 model 覆盖，后续将使用 Codex 会话当前默认模型。",
            )
            return
        await send_interaction_message(interaction, f"当前线程已设置 model 覆盖：`{override}`")

    @group.command(name="set", description="为当前 Discord 线程设置 model 覆盖")
    @app_commands.describe(model="模型名，例如：gpt-5.2 / o3 / o4-mini 等（按你本机 Codex 支持的为准）")
    async def set_model(interaction: discord.Interaction, model: str) -> None:
        normalized = _normalize_model(model)
        if normalized is None:
            await send_interaction_error(interaction, "model 不能为空。")
            return

        try:
            route = await _ensure_thread_session(interaction)
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return

        worker_key = str(interaction.channel.id)
        worker = app_state.worker_pool.get_worker(worker_key)
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(worker_key) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        await app_state.session_service.set_model_override(
            discord_thread_id=str(interaction.channel.id),
            model_override=normalized,
        )
        await app_state.audit_service.record(
            action="session_model_override_set",
            guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
            discord_thread_id=str(interaction.channel.id),
            actor_id=str(interaction.user.id),
            payload={
                "codex_thread_id": getattr(route.session, "codex_thread_id", None),
                "model_override": normalized,
            },
        )
        await send_interaction_message(interaction, f"已设置当前线程 model：`{normalized}`。后续消息将使用该模型。")

    @group.command(name="clear", description="清除当前 Discord 线程的 model 覆盖")
    async def clear_model(interaction: discord.Interaction) -> None:
        try:
            route = await _ensure_thread_session(interaction)
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return

        worker_key = str(interaction.channel.id)
        worker = app_state.worker_pool.get_worker(worker_key)
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(worker_key) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        await app_state.session_service.set_model_override(
            discord_thread_id=str(interaction.channel.id),
            model_override=None,
        )
        await app_state.audit_service.record(
            action="session_model_override_cleared",
            guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
            discord_thread_id=str(interaction.channel.id),
            actor_id=str(interaction.user.id),
            payload={
                "codex_thread_id": getattr(route.session, "codex_thread_id", None),
            },
        )
        await send_interaction_message(
            interaction,
            "已清除当前线程的 model 覆盖。后续将使用 Codex 会话当前默认模型。",
        )

    return group

