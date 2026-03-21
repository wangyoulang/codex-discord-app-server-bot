from __future__ import annotations

import discord
from discord import app_commands

from codex_discord_bot.discord.handlers.interactions import send_interaction_error


def build_group(app_state) -> app_commands.Group:
    group = app_commands.Group(name="project", description="项目与工作区管理")

    @group.command(name="add", description="注册论坛频道为工作区")
    @app_commands.describe(
        name="工作区名称",
        cwd="本地代码目录绝对路径",
        forum_channel="绑定到该工作区的论坛频道",
        model="默认模型，例如 gpt-5.4",
    )
    async def add(
        interaction: discord.Interaction,
        name: str,
        cwd: str,
        forum_channel: discord.ForumChannel,
        model: str | None = None,
    ) -> None:
        if interaction.guild is None:
            await send_interaction_error(interaction, "该命令只能在服务器内使用。")
            return

        try:
            workspace = await app_state.workspace_service.create_workspace(
                guild_id=str(interaction.guild.id),
                forum_channel_id=str(forum_channel.id),
                name=name,
                cwd=cwd,
                default_model=model or app_state.settings.codex_model,
                default_reasoning_effort=app_state.settings.codex_reasoning_effort,
                sandbox_mode=app_state.settings.codex_sandbox_mode,
                approval_policy=app_state.settings.codex_approval_policy,
            )
            await app_state.audit_service.record(
                action="workspace_created",
                guild_id=str(interaction.guild.id),
                actor_id=str(interaction.user.id),
                payload={
                    "workspace_id": workspace.id,
                    "forum_channel_id": str(forum_channel.id),
                    "cwd": cwd,
                },
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return

        await interaction.response.send_message(
            f"工作区已创建：`{workspace.name}` -> <#{forum_channel.id}>",
            ephemeral=True,
        )

    @group.command(name="list", description="列出当前服务器的工作区")
    async def list_workspaces(interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await send_interaction_error(interaction, "该命令只能在服务器内使用。")
            return

        workspaces = await app_state.workspace_service.list_workspaces(
            guild_id=str(interaction.guild.id)
        )
        if not workspaces:
            await interaction.response.send_message("当前服务器还没有已注册工作区。", ephemeral=True)
            return

        lines = [
            f"- `{workspace.name}` | forum=<#{workspace.forum_channel_id}> | cwd=`{workspace.cwd}`"
            for workspace in workspaces
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    return group
