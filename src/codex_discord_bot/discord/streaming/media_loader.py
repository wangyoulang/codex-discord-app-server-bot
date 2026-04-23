from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codex_discord_bot.codex.stream_renderer import SUPPORTED_OUTPUT_IMAGE_SUFFIXES


@dataclass(slots=True)
class LoadedOutboundImage:
    path: Path
    size: int


def load_outbound_image(
    raw_path: str | Path,
    *,
    max_bytes: int,
    workspace_cwd: str | Path | None = None,
    runtime_cwd: str | Path | None = None,
) -> LoadedOutboundImage:
    path = _resolve_outbound_image_path(
        raw_path,
        workspace_cwd=workspace_cwd,
        runtime_cwd=runtime_cwd,
    )
    if path.suffix.lower() not in SUPPORTED_OUTPUT_IMAGE_SUFFIXES:
        raise ValueError(f"不支持的图片类型：{path}")
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在：{path}")
    if not path.is_file():
        raise ValueError(f"图片路径不是普通文件：{path}")

    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"图片文件超过大小限制：{path}")
    return LoadedOutboundImage(path=path, size=size)


def _resolve_outbound_image_path(
    raw_path: str | Path,
    *,
    workspace_cwd: str | Path | None,
    runtime_cwd: str | Path | None,
) -> Path:
    path = Path(raw_path).expanduser().resolve(strict=False)
    if path.exists():
        return path

    fallback_path = _recover_missing_workspace_image_path(
        path,
        workspace_cwd=workspace_cwd,
        runtime_cwd=runtime_cwd,
    )
    if fallback_path is not None:
        return fallback_path
    return path


def _recover_missing_workspace_image_path(
    path: Path,
    *,
    workspace_cwd: str | Path | None,
    runtime_cwd: str | Path | None,
) -> Path | None:
    workspace_root = _normalize_optional_directory(workspace_cwd)
    if workspace_root is None or not path.is_absolute():
        return None

    try:
        relative_path = path.relative_to(workspace_root)
    except ValueError:
        return None

    runtime_root = _normalize_optional_directory(runtime_cwd) or Path.cwd().resolve(strict=False)
    candidates = [
        (runtime_root / relative_path).resolve(strict=False),
        (runtime_root / path.name).resolve(strict=False),
    ]

    for candidate in candidates:
        if candidate == path:
            continue
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _normalize_optional_directory(raw_path: str | Path | None) -> Path | None:
    if raw_path is None:
        return None
    return Path(raw_path).expanduser().resolve(strict=False)
