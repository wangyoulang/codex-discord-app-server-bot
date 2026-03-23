from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


ChunkMode = Literal["length", "newline"]

DEFAULT_MAX_CHARS = 2000
DEFAULT_MAX_LINES = 17
FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")


@dataclass(slots=True)
class OpenFence:
    indent: str
    marker_char: str
    marker_len: int
    open_line: str


def chunk_discord_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_lines: int = DEFAULT_MAX_LINES,
    chunk_mode: ChunkMode = "length",
) -> list[str]:
    if not text:
        return []

    if chunk_mode == "newline":
        chunks: list[str] = []
        for block in _split_newline_candidates(text, max_chars=max_chars):
            nested = _chunk_core(block, max_chars=max_chars, max_lines=max_lines)
            if nested:
                chunks.extend(nested)
            elif block:
                chunks.append(block)
        return _resolve_text_chunks_with_fallback(text, chunks)

    return _resolve_text_chunks_with_fallback(
        text,
        _chunk_core(text, max_chars=max_chars, max_lines=max_lines),
    )


def _split_newline_candidates(text: str, *, max_chars: int) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        addition_len = len(line) + (1 if current else 0)
        if current and (line == "" or current_len + addition_len > max_chars):
            blocks.append("\n".join(current))
            current = []
            current_len = 0

        current.append(line)
        current_len += len(line) + (1 if current_len else 0)

    if current:
        blocks.append("\n".join(current))
    return blocks


def _chunk_core(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    body = text or ""
    if not body:
        return []

    if len(body) <= max_chars and _count_lines(body) <= max_lines:
        return [body]

    chunks: list[str] = []
    current = ""
    current_lines = 0
    open_fence: OpenFence | None = None

    def flush() -> None:
        nonlocal current, current_lines
        if not current:
            return
        payload = _close_fence_if_needed(current, open_fence)
        if payload.strip():
            chunks.append(payload)
        current = ""
        current_lines = 0
        if open_fence is not None:
            current = open_fence.open_line
            current_lines = 1

    for original_line in body.split("\n"):
        fence_info = _parse_fence_line(original_line)
        was_inside_fence = open_fence is not None
        next_open_fence = open_fence
        if fence_info is not None:
            if open_fence is None:
                next_open_fence = fence_info
            elif (
                open_fence.marker_char == fence_info.marker_char
                and fence_info.marker_len >= open_fence.marker_len
            ):
                next_open_fence = None

        reserve_chars = len(_close_fence_line(next_open_fence)) + 1 if next_open_fence else 0
        reserve_lines = 1 if next_open_fence else 0
        effective_max_chars = max(max_chars - reserve_chars, 1)
        effective_max_lines = max(max_lines - reserve_lines, 1)
        prefix_len = len(current) + 1 if current else 0
        segment_limit = max(effective_max_chars - prefix_len, 1)
        segments = _split_long_line(
            original_line,
            max_chars=segment_limit,
            preserve_whitespace=was_inside_fence,
        )

        for seg_index, segment in enumerate(segments):
            is_continuation = seg_index > 0
            delimiter = "" if is_continuation else ("\n" if current else "")
            addition = f"{delimiter}{segment}"
            next_len = len(current) + len(addition)
            next_lines = current_lines + (0 if is_continuation else 1)

            if (next_len > effective_max_chars or next_lines > effective_max_lines) and current:
                flush()

            if current:
                current += addition
                if not is_continuation:
                    current_lines += 1
            else:
                current = segment
                current_lines = 1

        open_fence = next_open_fence

    if current:
        payload = _close_fence_if_needed(current, open_fence)
        if payload.strip():
            chunks.append(payload)
    return chunks


def _count_lines(text: str) -> int:
    return len(text.split("\n")) if text else 0


def _parse_fence_line(line: str) -> OpenFence | None:
    match = FENCE_RE.match(line)
    if match is None:
        return None
    indent = match.group(1) or ""
    marker = match.group(2) or ""
    return OpenFence(
        indent=indent,
        marker_char=marker[0] if marker else "`",
        marker_len=len(marker),
        open_line=line,
    )


def _close_fence_line(open_fence: OpenFence | None) -> str:
    if open_fence is None:
        return ""
    return f"{open_fence.indent}{open_fence.marker_char * open_fence.marker_len}"


def _close_fence_if_needed(text: str, open_fence: OpenFence | None) -> str:
    if open_fence is None:
        return text
    close_line = _close_fence_line(open_fence)
    if not text:
        return close_line
    if text.endswith("\n"):
        return f"{text}{close_line}"
    return f"{text}\n{close_line}"


def _split_long_line(
    line: str,
    *,
    max_chars: int,
    preserve_whitespace: bool,
) -> list[str]:
    if len(line) <= max_chars:
        return [line]

    chunks: list[str] = []
    remaining = line
    while len(remaining) > max_chars:
        if preserve_whitespace:
            chunks.append(remaining[:max_chars])
            remaining = remaining[max_chars:]
            continue

        window = remaining[:max_chars]
        break_index = -1
        for index in range(len(window) - 1, -1, -1):
            if window[index].isspace():
                break_index = index
                break
        if break_index <= 0:
            break_index = max_chars
        chunks.append(remaining[:break_index])
        remaining = remaining[break_index:]

    if remaining:
        chunks.append(remaining)
    return chunks


def _resolve_text_chunks_with_fallback(text: str, chunks: list[str]) -> list[str]:
    normalized = [chunk for chunk in chunks if chunk]
    if normalized:
        return normalized
    if text:
        return [text]
    return []
