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


def test_load_outbound_image_recovers_missing_workspace_path_from_runtime_cwd(tmp_path: Path) -> None:
    workspace_dir = tmp_path / 'workspace'
    runtime_dir = tmp_path / 'runtime'
    workspace_dir.mkdir()
    runtime_dir.mkdir()

    actual_image_path = runtime_dir / 'screen.png'
    actual_image_path.write_bytes(b'png-bytes')
    missing_workspace_path = workspace_dir / 'screen.png'

    loaded = load_outbound_image(
        missing_workspace_path,
        max_bytes=32,
        workspace_cwd=workspace_dir,
        runtime_cwd=runtime_dir,
    )

    assert loaded.path == actual_image_path.resolve()
    assert loaded.size == len(b'png-bytes')


def test_load_outbound_image_does_not_recover_missing_path_outside_workspace(tmp_path: Path) -> None:
    workspace_dir = tmp_path / 'workspace'
    runtime_dir = tmp_path / 'runtime'
    workspace_dir.mkdir()
    runtime_dir.mkdir()

    actual_image_path = runtime_dir / 'screen.png'
    actual_image_path.write_bytes(b'png-bytes')
    missing_external_path = tmp_path / 'external' / 'screen.png'

    with pytest.raises(FileNotFoundError):
        load_outbound_image(
            missing_external_path,
            max_bytes=32,
            workspace_cwd=workspace_dir,
            runtime_cwd=runtime_dir,
        )
