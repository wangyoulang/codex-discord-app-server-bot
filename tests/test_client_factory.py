from __future__ import annotations

from pathlib import Path

from codex_discord_bot.codex.client_factory import build_codex_config
from codex_discord_bot.config import Settings


def test_build_codex_config_reuses_current_codex_home_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "/existing/codex-home")

    settings = Settings(discord_bot_token="token", codex_home=None)
    config = build_codex_config(settings)

    assert config.env is not None
    assert config.env["CODEX_HOME"] == "/existing/codex-home"


def test_build_codex_config_overrides_codex_home_when_explicitly_configured(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "/existing/codex-home")

    settings = Settings(
        discord_bot_token="token",
        codex_home=Path("/override/codex-home"),
    )
    config = build_codex_config(settings)

    assert config.env is not None
    assert config.env["CODEX_HOME"] == "/override/codex-home"
