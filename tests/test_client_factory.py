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


def test_build_codex_config_applies_explicit_codex_proxy_settings(monkeypatch) -> None:
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    settings = Settings(
        discord_bot_token="token",
        codex_http_proxy="http://127.0.0.1:7890",
        codex_https_proxy="http://127.0.0.1:7890",
        codex_all_proxy="socks5://127.0.0.1:7890",
        codex_no_proxy="127.0.0.1,localhost",
    )
    config = build_codex_config(settings)

    assert config.env is not None
    assert config.env["http_proxy"] == "http://127.0.0.1:7890"
    assert config.env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert config.env["https_proxy"] == "http://127.0.0.1:7890"
    assert config.env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert config.env["all_proxy"] == "socks5://127.0.0.1:7890"
    assert config.env["ALL_PROXY"] == "socks5://127.0.0.1:7890"
    assert config.env["no_proxy"] == "127.0.0.1,localhost"
    assert config.env["NO_PROXY"] == "127.0.0.1,localhost"


def test_build_codex_config_proxy_settings_override_inherited_env(monkeypatch) -> None:
    monkeypatch.setenv("https_proxy", "http://old-proxy:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://old-proxy:8080")

    settings = Settings(
        discord_bot_token="token",
        codex_https_proxy="http://new-proxy:7890",
    )
    config = build_codex_config(settings)

    assert config.env is not None
    assert config.env["https_proxy"] == "http://new-proxy:7890"
    assert config.env["HTTPS_PROXY"] == "http://new-proxy:7890"
