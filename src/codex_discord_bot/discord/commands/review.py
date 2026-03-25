from __future__ import annotations

from discord import app_commands

from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.providers.types import provider_display_name


def build_group(_app_state, provider: ProviderKind) -> app_commands.Group:
    provider_label = provider_display_name(provider)
    group = app_commands.Group(name="review", description=f"{provider_label} Code Review 能力")

    @group.command(name="start", description="启动 review（下一阶段接入）")
    async def start_review(interaction) -> None:
        await interaction.response.send_message(
            f"`{provider_label} review/start` 将在下一阶段接入。",
            ephemeral=True,
        )

    return group
