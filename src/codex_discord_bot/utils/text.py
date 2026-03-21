from __future__ import annotations


def truncate_text(text: str, *, limit: int = 1800) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def as_code_block(text: str) -> str:
    return f"```text\n{text}\n```"
