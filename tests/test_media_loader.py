from __future__ import annotations

from pathlib import Path

import pytest

from codex_discord_bot.discord.streaming.media_loader import load_outbound_image


def test_load_outbound_image_accepts_existing_image_file(tmp_path: Path) -> None:
    image_path = tmp_path / 'screen.png'
    image_path.write_bytes(b'png-bytes')

    loaded = load_outbound_image(image_path, max_bytes=32)

    assert loaded.path == image_path.resolve()
    assert loaded.size == len(b'png-bytes')


def test_load_outbound_image_rejects_unsupported_suffix(tmp_path: Path) -> None:
    file_path = tmp_path / 'note.txt'
    file_path.write_text('not image')

    with pytest.raises(ValueError):
        load_outbound_image(file_path, max_bytes=1024)


def test_load_outbound_image_rejects_oversized_file(tmp_path: Path) -> None:
    image_path = tmp_path / 'screen.png'
    image_path.write_bytes(b'0123456789')

    with pytest.raises(ValueError):
        load_outbound_image(image_path, max_bytes=4)
