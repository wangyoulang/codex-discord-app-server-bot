from __future__ import annotations

from pathlib import Path
import json
from typing import Literal

from pydantic import Field
from pydantic import field_validator
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
    codex_session_source: str = "discord-bot"

    enable_codex_command: bool = True
    enable_claude_command: bool = False

    claude_bin: str = "claude"
    claude_model: str = "sonnet"
    claude_effort: Literal["low", "medium", "high", "max"] = "medium"
    claude_permission_mode: Literal[
        "default",
        "acceptEdits",
        "plan",
        "bypassPermissions",
    ] = "default"
    claude_setting_sources: str = "user,project,local"
    claude_include_partial_messages: bool = True
    claude_auth_mode: Literal[
        "api_key",
        "auth_token",
        "local_login",
        "bedrock",
        "vertex",
    ] = "api_key"
    claude_api_key: str | None = None
    claude_auth_token: str | None = None
    claude_base_url: str | None = None
    claude_custom_headers_json: str | None = None
    claude_use_bedrock: bool = False
    claude_bedrock_base_url: str | None = None
    claude_skip_bedrock_auth: bool = False
    claude_use_vertex: bool = False
    claude_vertex_base_url: str | None = None
    claude_vertex_project_id: str | None = None
    claude_vertex_region: str | None = None
    claude_skip_vertex_auth: bool = False

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

    @field_validator("claude_custom_headers_json")
    @classmethod
    def validate_claude_custom_headers_json(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("CLAUDE_CUSTOM_HEADERS_JSON 必须是 JSON 对象")
        for key, item in parsed.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise ValueError("CLAUDE_CUSTOM_HEADERS_JSON 只能包含字符串键值")
        return value

    def parsed_claude_setting_sources(self) -> list[str]:
        return [item.strip() for item in self.claude_setting_sources.split(",") if item.strip()]

    def parsed_claude_custom_headers(self) -> dict[str, str]:
        if self.claude_custom_headers_json is None:
            return {}
        parsed = json.loads(self.claude_custom_headers_json)
        return {str(key): str(value) for key, value in parsed.items()}

    def ensure_runtime_dirs(self) -> None:
        for path in (self.state_dir, self.artifact_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)
        if self.codex_home is not None:
            self.codex_home.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
