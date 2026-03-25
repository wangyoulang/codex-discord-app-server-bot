from __future__ import annotations

from types import SimpleNamespace

from codex_discord_bot.discord.command_tree import register_commands


class FakeTree:
    def __init__(self) -> None:
        self.commands: list[tuple[str, int | None]] = []

    def add_command(self, command, guild=None) -> None:
        guild_id = getattr(guild, "id", None)
        self.commands.append((command.name, guild_id))


def test_register_commands_adds_both_roots_when_enabled() -> None:
    settings = SimpleNamespace(
        enable_codex_command=True,
        enable_claude_command=True,
        discord_guild_id=None,
    )
    bot = SimpleNamespace(app_state=SimpleNamespace(settings=settings), tree=FakeTree())

    register_commands(bot)

    assert [name for name, _guild in bot.tree.commands] == ["codex", "claude"]


def test_register_commands_respects_enable_flags() -> None:
    settings = SimpleNamespace(
        enable_codex_command=True,
        enable_claude_command=False,
        discord_guild_id=123456,
    )
    bot = SimpleNamespace(app_state=SimpleNamespace(settings=settings), tree=FakeTree())

    register_commands(bot)

    assert bot.tree.commands == [("codex", 123456)]
