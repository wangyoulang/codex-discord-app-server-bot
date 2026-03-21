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
        extra="ignore",
    )

    app_env: Literal["development", "production", "test"] = "development"

    discord_bot_token: str = Field(min_length=1)
    discord_application_id: int | None = None
    discord_guild_id: int | None = None
    discord_sync_guild_commands: bool = True

    database_url: str = "sqlite+aiosqlite:///./state/app.db"
    state_dir: Path = Path("./state")
    artifact_dir: Path = Path("./artifacts")
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"

    codex_bin: str | None = None
    codex_home: Path = Path("./runtime/codex-home")
    codex_model: str = "gpt-5.4"
    codex_reasoning_effort: str = "high"
    codex_sandbox_mode: str = "workspace-write"
    codex_approval_policy: str = "on-request"
    codex_service_tier: str = "fast"
    codex_default_personality: str = "pragmatic"

    worker_idle_timeout_seconds: int = 900

    def ensure_runtime_dirs(self) -> None:
        for path in (self.state_dir, self.artifact_dir, self.log_dir, self.codex_home):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
