from __future__ import annotations

import os

from codex_app_server import AppServerConfig
from codex_app_server import AsyncCodex

from codex_discord_bot.config import Settings


def build_codex_config(settings: Settings, *, cwd: str | None = None) -> AppServerConfig:
    env = os.environ.copy()
    env["CODEX_HOME"] = str(settings.codex_home)
    return AppServerConfig(
        codex_bin=settings.codex_bin,
        cwd=cwd,
        env=env,
        client_name="codex_discord_bot",
        client_title="Codex Discord Bot",
        client_version="0.1.0",
        experimental_api=True,
    )


def create_async_codex(settings: Settings, *, cwd: str | None = None) -> AsyncCodex:
    return AsyncCodex(config=build_codex_config(settings, cwd=cwd))
