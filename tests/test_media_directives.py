from __future__ import annotations

from pathlib import Path

from codex_discord_bot.codex.media_directives import parse_media_directives_from_messages
from codex_discord_bot.codex.media_directives import parse_media_directives_from_text
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot


def test_parse_media_directives_from_text_strips_media_lines_and_normalizes_paths(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    image_path = (assets_dir / "screen shot.png").resolve()

    parsed = parse_media_directives_from_text(
        "处理完成\nMEDIA: \"assets/screen shot.png\"\n请确认结果",
        item_id="item_1",
        workspace_cwd=str(tmp_path),
    )

    assert parsed.text == "处理完成\n请确认结果"
    assert len(parsed.media_artifacts) == 1
    artifact = parsed.media_artifacts[0]
    assert artifact.item_id == "item_1:media:0"
    assert artifact.path == str(image_path)
    assert artifact.source_type == "mediaDirective"
    assert artifact.parent_item_id == "item_1"


def test_parse_media_directives_from_text_supports_markdown_image_local_path(tmp_path: Path) -> None:
    image_path = (tmp_path / "monitor-dashboard.png").resolve()

    parsed = parse_media_directives_from_text(
        f"已直接贴图\n![监控大盘截图]({image_path})\n请继续分析",
        item_id="item_1",
        workspace_cwd=str(tmp_path),
    )

    assert parsed.text == "已直接贴图\n请继续分析"
    assert len(parsed.media_artifacts) == 1
    artifact = parsed.media_artifacts[0]
    assert artifact.path == str(image_path)
    assert artifact.source_type == "markdownImage"
    assert artifact.parent_item_id == "item_1"


def test_parse_media_directives_from_text_supports_markdown_link_local_path(tmp_path: Path) -> None:
    image_path = (tmp_path / "monitor-dashboard.png").resolve()

    parsed = parse_media_directives_from_text(
        f"前文\n[monitor-dashboard.png]({image_path})\n后文",
        item_id="item_1",
        workspace_cwd=str(tmp_path),
    )

    assert parsed.text == "前文\n后文"
    assert len(parsed.media_artifacts) == 1
    artifact = parsed.media_artifacts[0]
    assert artifact.path == str(image_path)
    assert artifact.source_type == "markdownLink"
    assert artifact.parent_item_id == "item_1"


def test_parse_media_directives_from_text_supports_inline_markdown_link_local_path(tmp_path: Path) -> None:
    image_path = (tmp_path / "monitor-dashboard.png").resolve()

    parsed = parse_media_directives_from_text(
        f"文件在 [monitor-dashboard.png]({image_path})，请直接展示",
        item_id="item_1",
        workspace_cwd=str(tmp_path),
    )

    assert parsed.text == "文件在，请直接展示"
    assert len(parsed.media_artifacts) == 1
    artifact = parsed.media_artifacts[0]
    assert artifact.path == str(image_path)
    assert artifact.source_type == "markdownLink"
    assert artifact.parent_item_id == "item_1"


def test_parse_media_directives_from_text_keeps_invalid_non_image_lines() -> None:
    parsed = parse_media_directives_from_text(
        "说明\nMEDIA: /tmp/result.txt\n结束",
        item_id="item_1",
        workspace_cwd="/repo",
    )

    assert parsed.text == "说明\nMEDIA: /tmp/result.txt\n结束"
    assert parsed.media_artifacts == []


def test_parse_media_directives_from_text_keeps_remote_markdown_links() -> None:
    parsed = parse_media_directives_from_text(
        "说明\n![线上图片](https://example.com/monitor.png)\n结束",
        item_id="item_1",
        workspace_cwd="/repo",
    )

    assert parsed.text == "说明\n![线上图片](https://example.com/monitor.png)\n结束"
    assert parsed.media_artifacts == []


def test_parse_media_directives_from_text_keeps_inline_remote_markdown_links() -> None:
    parsed = parse_media_directives_from_text(
        "文件在 [线上图片](https://example.com/monitor.png)，请查看",
        item_id="item_1",
        workspace_cwd="/repo",
    )

    assert parsed.text == "文件在 [线上图片](https://example.com/monitor.png)，请查看"
    assert parsed.media_artifacts == []


def test_parse_media_directives_from_messages_supports_file_urls(tmp_path: Path) -> None:
    image_path = (tmp_path / "capture.webp").resolve()
    snapshots = [
        AssistantMessageSnapshot(
            item_id="item_1",
            text=f"先看图\nMEDIA: file://{image_path}\n再继续",
        )
    ]

    cleaned_snapshots, artifacts = parse_media_directives_from_messages(
        snapshots,
        workspace_cwd=str(tmp_path),
    )

    assert cleaned_snapshots == [AssistantMessageSnapshot(item_id="item_1", text="先看图\n再继续")]
    assert len(artifacts) == 1
    assert artifacts[0].path == str(image_path)
    assert artifacts[0].parent_item_id == "item_1"
