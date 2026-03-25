from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from codex_discord_bot.config import Settings

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions


def build_claude_env(settings: Settings) -> dict[str, str]:
    env: dict[str, str] = {}

    if settings.claude_auth_mode == "api_key":
        if settings.claude_api_key:
            env["ANTHROPIC_API_KEY"] = settings.claude_api_key
    elif settings.claude_auth_mode == "auth_token":
        if settings.claude_auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = settings.claude_auth_token
    elif settings.claude_auth_mode == "bedrock":
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        if settings.claude_skip_bedrock_auth:
            env["CLAUDE_CODE_SKIP_BEDROCK_AUTH"] = "1"
    elif settings.claude_auth_mode == "vertex":
        env["CLAUDE_CODE_USE_VERTEX"] = "1"
        if settings.claude_skip_vertex_auth:
            env["CLAUDE_CODE_SKIP_VERTEX_AUTH"] = "1"

    if settings.claude_base_url:
        env["ANTHROPIC_BASE_URL"] = settings.claude_base_url
    if settings.claude_bedrock_base_url:
        env["ANTHROPIC_BEDROCK_BASE_URL"] = settings.claude_bedrock_base_url
    if settings.claude_vertex_base_url:
        env["ANTHROPIC_VERTEX_BASE_URL"] = settings.claude_vertex_base_url
    if settings.claude_vertex_project_id:
        env["CLOUD_ML_PROJECT_ID"] = settings.claude_vertex_project_id
    if settings.claude_vertex_region:
        env["CLOUD_ML_REGION"] = settings.claude_vertex_region

    headers = settings.parsed_claude_custom_headers()
    if headers:
        env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(
            f"{name}: {value}" for name, value in headers.items()
        )

    return env


def build_claude_options(
    settings: Settings,
    *,
    cwd: str,
    resume_session_id: str | None,
    can_use_tool,
) -> "ClaudeAgentOptions":
    try:
        from claude_agent_sdk import ClaudeAgentOptions
    except ImportError as exc:
        raise RuntimeError(
            "当前环境缺少 claude-agent-sdk，请先执行 `uv sync` 安装依赖。"
        ) from exc

    env = build_claude_env(settings)
    option_kwargs = {
        "cwd": cwd,
        "cli_path": settings.claude_bin,
        "model": settings.claude_model,
        "env": env,
        "permission_mode": settings.claude_permission_mode,
        "setting_sources": settings.parsed_claude_setting_sources(),
        "include_partial_messages": settings.claude_include_partial_messages,
        "effort": settings.claude_effort,
        "can_use_tool": can_use_tool,
    }
    if resume_session_id:
        option_kwargs["resume"] = resume_session_id
    return ClaudeAgentOptions(**option_kwargs)


def validate_claude_runtime(settings: Settings) -> None:
    if not settings.enable_claude_command:
        return

    cli_path = settings.claude_bin
    if os.path.sep in cli_path:
        if not Path(cli_path).exists():
            raise RuntimeError(f"找不到 Claude CLI：`{cli_path}`")
    elif not any(
        Path(parent, cli_path).exists()
        for parent in os.environ.get("PATH", "").split(os.pathsep)
        if parent
    ):
        raise RuntimeError(f"找不到 Claude CLI：`{cli_path}`")

    if settings.claude_auth_mode == "api_key" and not settings.claude_api_key:
        raise RuntimeError("已启用 /claude，但 CLAUDE_API_KEY 为空。")
    if settings.claude_auth_mode == "auth_token" and not settings.claude_auth_token:
        raise RuntimeError("已启用 /claude，但 CLAUDE_AUTH_TOKEN 为空。")
