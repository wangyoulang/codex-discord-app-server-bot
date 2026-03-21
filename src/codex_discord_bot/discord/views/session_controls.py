from __future__ import annotations

import discord


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
        await interaction.response.send_message("会话控制按钮骨架已就绪。", ephemeral=True)
