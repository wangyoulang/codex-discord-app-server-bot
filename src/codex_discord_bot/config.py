from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = "development"

    discord_bot_token: str = Field(min_length=1)
    discord_application_id: int | None = None
    discord_guild_id: int | None = None
    discord_sync_guild_commands: bool = True
    discord_proxy_url: str | None = None

    database_url: str = "sqlite+aiosqlite:///./state/app.db"
    state_dir: Path = Path("./state")
    artifact_dir: Path = Path("./artifacts")
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"

    codex_bin: str | None = None
    codex_home: Path | None = None
    codex_http_proxy: str | None = None
    codex_https_proxy: str | None = None
    codex_all_proxy: str | None = None
    codex_no_proxy: str | None = None
    worker_idle_timeout_seconds: int = 900

    discord_preview_mode: Literal["off", "partial", "block"] = "off"
    discord_preview_throttle_ms: int = 1200
    discord_preview_min_initial_chars: int = 30
    discord_block_preview_min_chars: int = 200
    discord_block_preview_max_chars: int = 800
    discord_block_preview_break_preference: Literal["paragraph", "newline", "sentence"] = (
        "paragraph"
    )
    discord_final_max_lines_per_message: int = 17
    discord_reply_to_mode: Literal["none", "first", "all"] = "first"

    def ensure_runtime_dirs(self) -> None:
        for path in (self.state_dir, self.artifact_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
        if self.codex_home is not None:
            self.codex_home.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
