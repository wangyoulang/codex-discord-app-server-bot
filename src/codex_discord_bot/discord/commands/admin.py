from __future__ import annotations

import discord
from discord import app_commands


def build_group(_app_state) -> app_commands.Group:
    group = app_commands.Group(name="admin", description="管理命令")

    @group.command(name="sync", description="同步当前 guild 的 slash commands")
    async def sync_commands(interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("该命令只能在服务器内使用。", ephemeral=True)
            return

        synced = await interaction.client.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(
            f"已同步 {len(synced)} 条 guild 命令。",
            ephemeral=True,
        )

    return group
