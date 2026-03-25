from __future__ import annotations

import discord
from discord import app_commands

from codex_discord_bot.discord.commands import admin
from codex_discord_bot.discord.commands import project
from codex_discord_bot.discord.commands import review
from codex_discord_bot.discord.commands import session
from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.providers.types import provider_display_name
from codex_discord_bot.providers.types import provider_root_command


def _build_root_group(bot, provider: ProviderKind) -> app_commands.Group:
    label = provider_display_name(provider)
    root = app_commands.Group(
        name=provider_root_command(provider),
        description=f"{label} Discord 协作机器人",
    )
    root.add_command(project.build_group(bot.app_state, provider))
    root.add_command(session.build_group(bot.app_state, provider))
    root.add_command(review.build_group(bot.app_state, provider))
    root.add_command(admin.build_group(bot.app_state, provider))
    return root


def register_commands(bot) -> None:
    guild = None
    if bot.app_state.settings.discord_guild_id is not None:
        guild = discord.Object(id=bot.app_state.settings.discord_guild_id)

    if bot.app_state.settings.enable_codex_command:
        bot.tree.add_command(_build_root_group(bot, ProviderKind.codex), guild=guild)
    if bot.app_state.settings.enable_claude_command:
        bot.tree.add_command(_build_root_group(bot, ProviderKind.claude), guild=guild)
