from __future__ import annotations

from codex_discord_bot.discord.streaming.chunker import chunk_discord_text


def test_chunk_discord_text_splits_tall_messages_under_2000_chars() -> None:
    text = "\n".join(f"line-{index}" for index in range(1, 46))
    chunks = chunk_discord_text(text, max_chars=2000, max_lines=20)

    assert len(chunks) > 1
    assert all(len(chunk.splitlines()) <= 20 for chunk in chunks)


def test_chunk_discord_text_keeps_fenced_code_balanced() -> None:
    body = "\n".join(f"console.log({index});" for index in range(30))
    text = f"Here is code:\n\n```js\n{body}\n```\n\nDone."

    chunks = chunk_discord_text(text, max_chars=2000, max_lines=10)

    assert len(chunks) > 1
    assert all(chunk.count("```") % 2 == 0 for chunk in chunks)
    assert all(len(chunk) <= 2000 for chunk in chunks)
