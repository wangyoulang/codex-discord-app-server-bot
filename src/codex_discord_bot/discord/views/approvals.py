from __future__ import annotations

import discord


class ApprovalDecisionView(discord.ui.View):
    def __init__(self, app_state: object) -> None:
        super().__init__(timeout=None)
        self.app_state = app_state

    @discord.ui.button(label="批准", style=discord.ButtonStyle.success, custom_id="approval:accept")
    async def accept(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message("审批回写将在下一阶段接入。", ephemeral=True)

    @discord.ui.button(label="拒绝", style=discord.ButtonStyle.danger, custom_id="approval:decline")
    async def decline(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message("审批回写将在下一阶段接入。", ephemeral=True)
