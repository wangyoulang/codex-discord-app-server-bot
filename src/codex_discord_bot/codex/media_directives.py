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
_MARKDOWN_IMAGE_RE = re.compile(r"!\[(.*?)\]\((.+?)\)")
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[(.*?)\]\((.+?)\)")
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\/]")
_PUNCTUATION_SPACE_RE = re.compile(r"[ \t]+([,.;:!?，。；：！？])")


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
        cleaned_line, line_artifacts = _parse_media_line(
            line,
            item_id=item_id,
            artifact_index=len(artifacts),
            workspace_cwd=workspace_cwd,
        )
        if line_artifacts:
            if cleaned_line:
                kept_lines.append(cleaned_line)
            artifacts.extend(line_artifacts)
            continue
        kept_lines.append(line)

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

    parsed = urlparse(candidate)
    if parsed.scheme:
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                return None
            candidate = unquote(parsed.path)
        elif not _WINDOWS_DRIVE_RE.match(candidate):
            return None

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


def _parse_media_line(
    line: str,
    *,
    item_id: str,
    artifact_index: int,
    workspace_cwd: str | None,
) -> tuple[str, list[OutputImageArtifact]]:
    media_match = _MEDIA_DIRECTIVE_RE.match(line)
    if media_match is not None:
        artifact = _build_media_artifact(
            raw_path=media_match.group(1),
            item_id=item_id,
            artifact_index=artifact_index,
            workspace_cwd=workspace_cwd,
            source_type="mediaDirective",
        )
        return ("", [artifact]) if artifact is not None else ("", [])

    return _extract_inline_markdown_media(
        line,
        item_id=item_id,
        artifact_index=artifact_index,
        workspace_cwd=workspace_cwd,
    )


def _extract_inline_markdown_media(
    line: str,
    *,
    item_id: str,
    artifact_index: int,
    workspace_cwd: str | None,
) -> tuple[str, list[OutputImageArtifact]]:
    artifacts: list[OutputImageArtifact] = []
    kept_parts: list[str] = []
    cursor = 0

    for match, source_type in _iter_markdown_matches(line):
        artifact = _build_media_artifact(
            raw_path=match.group(2),
            item_id=item_id,
            artifact_index=artifact_index + len(artifacts),
            workspace_cwd=workspace_cwd,
            source_type=source_type,
        )
        if artifact is None:
            continue

        start, end = match.span()
        kept_parts.append(line[cursor:start])
        cursor = end
        artifacts.append(artifact)

    if not artifacts:
        return "", []

    kept_parts.append(line[cursor:])
    cleaned_line = _normalize_cleaned_line("".join(kept_parts))
    return cleaned_line, artifacts


def _iter_markdown_matches(line: str) -> list[tuple[re.Match[str], str]]:
    matches: list[tuple[re.Match[str], str]] = []
    matches.extend((match, "markdownImage") for match in _MARKDOWN_IMAGE_RE.finditer(line))
    matches.extend((match, "markdownLink") for match in _MARKDOWN_LINK_RE.finditer(line))
    matches.sort(key=lambda item: item[0].start())
    return matches


def _normalize_cleaned_line(value: str) -> str:
    collapsed_spaces = re.sub(r"[ \t]{2,}", " ", value)
    without_punctuation_space = _PUNCTUATION_SPACE_RE.sub(r"\1", collapsed_spaces)
    return without_punctuation_space.strip()


def _build_media_artifact(
    *,
    raw_path: str,
    item_id: str,
    artifact_index: int,
    workspace_cwd: str | None,
    source_type: str,
) -> OutputImageArtifact | None:
    image_path = normalize_media_directive_path(raw_path, workspace_cwd=workspace_cwd)
    if image_path is None:
        return None
    return OutputImageArtifact(
        item_id=f"{item_id}:media:{artifact_index}",
        path=str(image_path),
        source_type=source_type,
        parent_item_id=item_id,
    )


def _unwrap_wrapped_value(value: str) -> str:
    if len(value) < 2:
        return value
    if value[0] != value[-1]:
        return value
    if value[0] not in {'"', "'", '`'}:
        return value
    return value[1:-1].strip()
