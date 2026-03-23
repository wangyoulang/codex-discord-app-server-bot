from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True)
class PreviewChunkingConfig:
    min_chars: int
    max_chars: int
    break_preference: str = "paragraph"


class PreviewTextChunker:
    def __init__(self, config: PreviewChunkingConfig) -> None:
        self.config = config
        self._buffer = ""

    def append(self, text: str) -> None:
        if text:
            self._buffer += text

    def reset(self) -> None:
        self._buffer = ""

    def has_buffered(self) -> bool:
        return bool(self._buffer)

    def drain(self, *, force: bool) -> list[str]:
        chunks: list[str] = []
        while self._buffer:
            boundary = self._resolve_boundary(force=force)
            if boundary is None:
                break
            chunk = self._buffer[:boundary]
            self._buffer = self._buffer[boundary:]
            if chunk:
                chunks.append(chunk)
            force = False
        return chunks

    def _resolve_boundary(self, *, force: bool) -> int | None:
        max_chars = max(1, self.config.max_chars)
        min_chars = max(1, min(self.config.min_chars, max_chars))
        buffer_len = len(self._buffer)
        if buffer_len == 0:
            return None
        if force:
            return buffer_len

        search_len = min(buffer_len, max_chars)
        if search_len < min_chars:
            return None

        preferred = self._find_break(search_len)
        if preferred is not None and preferred >= min_chars:
            return preferred
        if buffer_len >= max_chars:
            return max_chars
        return None

    def _find_break(self, limit: int) -> int | None:
        segment = self._buffer[:limit]
        strategies = {
            "paragraph": [self._find_paragraph_break, self._find_newline_break, self._find_sentence_break],
            "newline": [self._find_newline_break, self._find_paragraph_break, self._find_sentence_break],
            "sentence": [self._find_sentence_break, self._find_paragraph_break, self._find_newline_break],
        }
        for strategy in strategies.get(self.config.break_preference, strategies["paragraph"]):
            boundary = strategy(segment)
            if boundary is not None:
                return boundary
        return None

    @staticmethod
    def _find_paragraph_break(text: str) -> int | None:
        index = text.rfind("\n\n")
        if index == -1:
            return None
        return index + 2

    @staticmethod
    def _find_newline_break(text: str) -> int | None:
        index = text.rfind("\n")
        if index == -1:
            return None
        return index + 1

    @staticmethod
    def _find_sentence_break(text: str) -> int | None:
        matches = list(re.finditer(r"(?<=[。！？!?\.])\s+", text))
        if not matches:
            return None
        return matches[-1].end()
