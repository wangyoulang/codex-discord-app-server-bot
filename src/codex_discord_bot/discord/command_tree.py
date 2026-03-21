from __future__ import annotations

import discord
from discord import app_commands

from codex_discord_bot.constants import ROOT_COMMAND_DESCRIPTION
from codex_discord_bot.constants import ROOT_COMMAND_NAME
from codex_discord_bot.discord.commands import admin
from codex_discord_bot.discord.commands import project
from codex_discord_bot.discord.commands import review
from codex_discord_bot.discord.commands import session


def register_commands(bot) -> None:
    root = app_commands.Group(name=ROOT_COMMAND_NAME, description=ROOT_COMMAND_DESCRIPTION)
    root.add_command(project.build_group(bot.app_state))
    root.add_command(session.build_group(bot.app_state))
    root.add_command(review.build_group(bot.app_state))
    root.add_command(admin.build_group(bot.app_state))

    guild = None
    if bot.app_state.settings.discord_guild_id is not None:
        guild = discord.Object(id=bot.app_state.settings.discord_guild_id)

    bot.tree.add_command(root, guild=guild)
