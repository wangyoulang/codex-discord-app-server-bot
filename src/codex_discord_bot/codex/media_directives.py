from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import unquote
from urllib.parse import urlparse

from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.stream_renderer import OutputImageArtifact
from codex_discord_bot.codex.stream_renderer import SUPPORTED_OUTPUT_IMAGE_SUFFIXES

_MEDIA_DIRECTIVE_RE = re.compile(r"^\s*MEDIA\s*:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(slots=True)
class ParsedMediaDirectiveText:
    text: str
    media_artifacts: list[OutputImageArtifact]


def parse_media_directives_from_text(
    text: str,
    *,
    item_id: str,
    workspace_cwd: str | None = None,
) -> ParsedMediaDirectiveText:
    if not text:
        return ParsedMediaDirectiveText(text="", media_artifacts=[])

    kept_lines: list[str] = []
    artifacts: list[OutputImageArtifact] = []
    for line in text.splitlines():
        match = _MEDIA_DIRECTIVE_RE.match(line)
        if match is None:
            kept_lines.append(line)
            continue

        image_path = normalize_media_directive_path(match.group(1), workspace_cwd=workspace_cwd)
        if image_path is None:
            kept_lines.append(line)
            continue

        artifacts.append(
            OutputImageArtifact(
                item_id=f"{item_id}:media:{len(artifacts)}",
                path=str(image_path),
                source_type="mediaDirective",
                parent_item_id=item_id,
            )
        )

    return ParsedMediaDirectiveText(text="\n".join(kept_lines), media_artifacts=artifacts)


def parse_media_directives_from_messages(
    snapshots: list[AssistantMessageSnapshot],
    *,
    workspace_cwd: str | None = None,
) -> tuple[list[AssistantMessageSnapshot], list[OutputImageArtifact]]:
    cleaned_snapshots: list[AssistantMessageSnapshot] = []
    artifacts: list[OutputImageArtifact] = []
    for snapshot in snapshots:
        parsed = parse_media_directives_from_text(
            snapshot.text,
            item_id=snapshot.item_id,
            workspace_cwd=workspace_cwd,
        )
        cleaned_snapshots.append(
            AssistantMessageSnapshot(
                item_id=snapshot.item_id,
                text=parsed.text,
            )
        )
        artifacts.extend(parsed.media_artifacts)
    return cleaned_snapshots, artifacts


def normalize_media_directive_path(raw_value: str, *, workspace_cwd: str | None = None) -> Path | None:
    candidate = _unwrap_wrapped_value(raw_value.strip())
    if not candidate:
        return None

    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        if parsed.scheme != "file":
            return None
        if parsed.netloc not in ("", "localhost"):
            return None
        candidate = unquote(parsed.path)

    if candidate.startswith("~"):
        candidate = str(Path(candidate).expanduser())

    path = Path(candidate)
    if not path.is_absolute():
        if workspace_cwd is not None:
            path = Path(workspace_cwd) / path
        else:
            path = path.resolve(strict=False)

    resolved = path.resolve(strict=False)
    if resolved.suffix.lower() not in SUPPORTED_OUTPUT_IMAGE_SUFFIXES:
        return None
    return resolved


def _unwrap_wrapped_value(value: str) -> str:
    if len(value) < 2:
        return value
    if value[0] != value[-1]:
        return value
    if value[0] not in {'"', "'", '`'}:
        return value
    return value[1:-1].strip()
