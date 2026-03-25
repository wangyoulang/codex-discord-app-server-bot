from __future__ import annotations

import pytest

from codex_discord_bot.claude.client_factory import build_claude_options
from codex_discord_bot.claude.client_factory import build_claude_env
from codex_discord_bot.claude.client_factory import validate_claude_runtime
from codex_discord_bot.config import Settings


def test_build_claude_env_supports_api_key_and_gateway_headers() -> None:
    settings = Settings(
        discord_bot_token="token",
        enable_claude_command=True,
        claude_auth_mode="api_key",
        claude_api_key="test-api-key",
        claude_base_url="https://gateway.example.com",
        claude_custom_headers_json='{"X-Test":"value","X-Trace":"abc"}',
    )

    env = build_claude_env(settings)

    assert env["ANTHROPIC_API_KEY"] == "test-api-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.example.com"
    assert env["ANTHROPIC_CUSTOM_HEADERS"] == "X-Test: value\nX-Trace: abc"


def test_build_claude_env_merges_extra_env_overrides() -> None:
    settings = Settings(
        discord_bot_token="token",
        enable_claude_command=True,
        claude_auth_mode="api_key",
        claude_api_key="test-api-key",
        claude_extra_env_json='{"CLAUDE_CODE_EFFORT_LEVEL":"max","X_TEST":"1"}',
    )

    env = build_claude_env(settings)

    assert env["ANTHROPIC_API_KEY"] == "test-api-key"
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max"
    assert env["X_TEST"] == "1"


def test_build_claude_options_supports_thinking_and_extra_args() -> None:
    settings = Settings(
        discord_bot_token="token",
        enable_claude_command=True,
        claude_auth_mode="api_key",
        claude_api_key="test-api-key",
        claude_thinking_mode="enabled",
        claude_thinking_budget_tokens=4096,
        claude_extra_args_json='{"--output-format":"json","--verbose":null}',
    )

    options = build_claude_options(
        settings,
        cwd="/repo",
        resume_session_id="session-1",
        can_use_tool=lambda *_args, **_kwargs: None,
    )

    assert options.resume == "session-1"
    assert options.thinking == {"type": "enabled", "budget_tokens": 4096}
    assert options.extra_args == {"--output-format": "json", "--verbose": None}


def test_settings_require_budget_tokens_when_thinking_enabled() -> None:
    with pytest.raises(ValueError, match="CLAUDE_THINKING_BUDGET_TOKENS"):
        Settings(
            discord_bot_token="token",
            enable_claude_command=True,
            claude_auth_mode="api_key",
            claude_api_key="test-api-key",
            claude_thinking_mode="enabled",
        )


def test_validate_claude_runtime_skips_checks_when_command_disabled() -> None:
    settings = Settings(
        discord_bot_token="token",
        enable_claude_command=False,
    )

    validate_claude_runtime(settings)


def test_validate_claude_runtime_requires_api_key_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/bin:/usr/bin")
    settings = Settings(
        discord_bot_token="token",
        enable_claude_command=True,
        claude_bin="/bin/echo",
        claude_auth_mode="api_key",
        claude_api_key=None,
    )

    with pytest.raises(RuntimeError, match="CLAUDE_API_KEY"):
        validate_claude_runtime(settings)
