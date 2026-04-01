from __future__ import annotations

import os

from codex_discord_bot.codex.app_server_client import AppServerConfig
from codex_discord_bot.config import Settings


def build_codex_config(settings: Settings, *, cwd: str | None = None) -> AppServerConfig:
    env = os.environ.copy()
    if settings.codex_home is not None:
        env["CODEX_HOME"] = str(settings.codex_home)
    _set_proxy_env(env, "http_proxy", settings.codex_http_proxy)
    _set_proxy_env(env, "https_proxy", settings.codex_https_proxy)
    _set_proxy_env(env, "all_proxy", settings.codex_all_proxy)
    _set_proxy_env(env, "no_proxy", settings.codex_no_proxy)
    return AppServerConfig(
        codex_bin=settings.codex_bin,
        cwd=cwd,
        env=env,
        client_name="codex_discord_bot",
        client_title="Codex Discord Bot",
        client_version="0.1.0",
        experimental_api=True,
    )


def _set_proxy_env(env: dict[str, str], key: str, value: str | None) -> None:
    if value is None or not value.strip():
        return
    normalized = value.strip()
    env[key] = normalized
    env[key.upper()] = normalized
