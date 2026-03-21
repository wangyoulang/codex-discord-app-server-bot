from __future__ import annotations

from discord import app_commands


def build_group(_app_state) -> app_commands.Group:
    group = app_commands.Group(name="review", description="Code Review 能力")

    @group.command(name="start", description="启动 review（下一阶段接入）")
    async def start_review(interaction) -> None:
        await interaction.response.send_message("`review/start` 将在下一阶段接入。", ephemeral=True)

    return group
