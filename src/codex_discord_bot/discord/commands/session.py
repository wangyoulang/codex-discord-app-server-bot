from __future__ import annotations

import discord
from discord import app_commands

from codex_discord_bot.discord.handlers.interactions import send_interaction_error


def build_group(app_state) -> app_commands.Group:
    group = app_commands.Group(name="session", description="会话管理")

    @group.command(name="new", description="为当前 Discord 线程初始化 Codex 会话")
    async def new_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
            async with app_state.worker_pool.lease(str(interaction.channel.id)) as worker:
                codex_thread = await worker.ensure_thread(route.session, route.workspace)
            await app_state.session_service.bind_codex_thread(
                discord_thread_id=str(interaction.channel.id),
                codex_thread_id=codex_thread.id,
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"初始化 Codex 会话失败：{exc}")
            return

        await interaction.response.send_message(
            f"Codex 会话已准备：`{codex_thread.id}`",
            ephemeral=True,
        )

    @group.command(name="status", description="查看当前 Discord 线程的会话状态")
    async def status(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        session = await app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session is None:
            await interaction.response.send_message("当前线程还没有会话记录。", ephemeral=True)
            return

        worker_active = app_state.worker_pool.has_worker(str(interaction.channel.id))
        await interaction.response.send_message(
            "\n".join(
                [
                    f"discord_thread_id: `{session.discord_thread_id}`",
                    f"codex_thread_id: `{session.codex_thread_id or '未创建'}`",
                    f"status: `{session.status.value}`",
                    f"worker_active: `{worker_active}`",
                ]
            ),
            ephemeral=True,
        )

    return group
