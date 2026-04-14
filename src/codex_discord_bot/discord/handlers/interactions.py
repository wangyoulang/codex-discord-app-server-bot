from __future__ import annotations

import discord


async def send_interaction_message(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=ephemeral)
        return
    await interaction.response.send_message(message, ephemeral=ephemeral)


async def send_interaction_error(
    interaction: discord.Interaction,
    message: str,
) -> None:
    await send_interaction_message(interaction, message, ephemeral=True)
