from __future__ import annotations

from pathlib import Path

from codex_discord_bot.codex.stream_renderer import output_images_from_items


def test_output_images_from_items_extracts_image_view_and_image_generation(tmp_path: Path) -> None:
    image_view_path = (tmp_path / 'view.png').resolve()
    image_generation_path = (tmp_path / 'generated.webp').resolve()

    artifacts = output_images_from_items(
        [
            {'id': 'img_view', 'type': 'imageView', 'path': str(image_view_path)},
            {
                'id': 'img_generation',
                'type': 'imageGeneration',
                'status': 'completed',
                'savedPath': str(image_generation_path),
            },
        ]
    )

    assert [(artifact.item_id, artifact.path, artifact.source_type) for artifact in artifacts] == [
        ('img_view', str(image_view_path), 'imageView'),
        ('img_generation', str(image_generation_path), 'imageGeneration'),
    ]


def test_output_images_from_items_skips_unsupported_paths_and_incomplete_generation(tmp_path: Path) -> None:
    relative_path = tmp_path / 'relative.png'
    absolute_text_path = (tmp_path / 'note.txt').resolve()

    artifacts = output_images_from_items(
        [
            {'id': 'img_relative', 'type': 'imageView', 'path': str(relative_path.relative_to(tmp_path))},
            {
                'id': 'img_incomplete',
                'type': 'imageGeneration',
                'status': 'inProgress',
                'savedPath': str((tmp_path / 'generated.png').resolve()),
            },
            {'id': 'img_text', 'type': 'imageView', 'path': str(absolute_text_path)},
        ]
    )

    assert artifacts == []
