from __future__ import annotations

import discord

from codex_discord_bot.discord.handlers.interactions import send_interaction_error
from codex_discord_bot.providers.types import provider_display_name


class SessionControlView(discord.ui.View):
    def __init__(self, app_state: object) -> None:
        super().__init__(timeout=None)
        self.app_state = app_state

    @discord.ui.button(label="状态", style=discord.ButtonStyle.secondary, custom_id="session:status")
    async def status(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中使用会话控制按钮。")
            return

        session = await self.app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session is None:
            await interaction.response.send_message("当前线程还没有会话记录。", ephemeral=True)
            return

        provider_label = provider_display_name(session.provider)
        worker = self.app_state.worker_pool.get_worker(session.provider, str(interaction.channel.id))
        active_turn = worker.get_active_turn() if worker is not None else None
        provider_thread = None
        if session.codex_thread_id is not None:
            provider_thread = await self.app_state.codex_thread_service.get_by_codex_thread_id(
                session.codex_thread_id,
                provider=session.provider,
            )
        await interaction.response.send_message(
            "\n".join(
                [
                    f"provider: `{session.provider.value}`",
                    f"discord_thread_id: `{session.discord_thread_id}`",
                    f"provider_thread_id: `{session.codex_thread_id or '未创建'}`",
                    f"source: `{provider_thread.source_label if provider_thread is not None and provider_thread.source_label else '未知'}`",
                    f"archived: `{provider_thread.archived if provider_thread is not None else '未知'}`",
                    f"bound_thread_id: `{provider_thread.bound_discord_thread_id if provider_thread is not None and provider_thread.bound_discord_thread_id is not None else '无'}`",
                    f"status: `{session.status.value}`",
                    f"active_turn_id: `{session.active_turn_id or '无'}`",
                    f"live_active_turn_id: `{active_turn.turn_id if active_turn is not None else '无'}`",
                    f"last_bot_message_id: `{session.last_bot_message_id or '无'}`",
                    f"worker_active: `{worker is not None}`",
                ]
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="打断",
        style=discord.ButtonStyle.danger,
        custom_id="session:interrupt",
    )
    async def interrupt(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中使用会话控制按钮。")
            return

        session = await self.app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session is None:
            await interaction.response.send_message("当前线程没有可打断的运行中 turn。", ephemeral=True)
            return

        provider_label = provider_display_name(session.provider)
        worker = self.app_state.worker_pool.get_worker(session.provider, str(interaction.channel.id))
        if worker is None:
            await interaction.response.send_message("当前线程没有可打断的运行中 turn。", ephemeral=True)
            return

        try:
            interrupted_turn_id = await worker.interrupt_active_turn()
        except Exception as exc:
            await interaction.response.send_message(
                f"请求打断失败：{exc}",
                ephemeral=True,
            )
            return
        if interrupted_turn_id is None:
            await interaction.response.send_message("当前线程没有可打断的运行中 turn。", ephemeral=True)
            return

        await self.app_state.audit_service.record(
            action="session_interrupt_requested",
            guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
            discord_thread_id=str(interaction.channel.id),
            actor_id=str(interaction.user.id),
            payload={"provider": session.provider.value, "turn_id": interrupted_turn_id},
        )
        await interaction.response.send_message(
            f"已请求打断当前 {provider_label} turn：`{interrupted_turn_id}`",
            ephemeral=True,
        )
