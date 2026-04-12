from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codex_discord_bot.codex.stream_renderer import SUPPORTED_OUTPUT_IMAGE_SUFFIXES


@dataclass(slots=True)
class LoadedOutboundImage:
    path: Path
    size: int


def load_outbound_image(raw_path: str | Path, *, max_bytes: int) -> LoadedOutboundImage:
    path = Path(raw_path).expanduser().resolve(strict=False)
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
