from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_events import ReasoningSummaryTextDeltaEvent
from codex_discord_bot.codex.stream_events import TokenUsageUpdatedEvent
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.stream_renderer import OutputImageArtifact
from codex_discord_bot.codex.token_usage import TokenUsageBreakdown
from codex_discord_bot.codex.token_usage import TokenUsageSnapshot
from codex_discord_bot.codex.worker import TurnRunResult
from codex_discord_bot.config import Settings
from codex_discord_bot.discord.streaming import delivery
from codex_discord_bot.discord.streaming.turn_output_controller import TurnOutputController
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.models import DiscordTurnOutput
from codex_discord_bot.services.turn_output_service import TurnOutputService


class FakeMessage:
    def __init__(
        self,
        channel: "FakeThread",
        message_id: int,
        content: str | None,
        *,
        edit_failures: int = 0,
    ) -> None:
        self.channel = channel
        self.id = message_id
        self.content = content
        self.deleted = False
        self.reference = None
        self.view = None
        self.file = None
        self.edit_failures = edit_failures
        self.edit_attempts = 0

    async def edit(self, *, content: str, view=None) -> None:  # noqa: ANN001
        self.edit_attempts += 1
        if self.edit_failures > 0:
            self.edit_failures -= 1
            raise TimeoutError("模拟 Discord 编辑超时")
        self.content = content
        self.view = view

    async def delete(self) -> None:
        self.deleted = True


class FakeThread:
    def __init__(self, channel_id: int, *, send_failures: int = 0) -> None:
        self.id = channel_id
        self.sent_messages: list[FakeMessage] = []
        self._next_id = 1000
        self.send_failures = send_failures
        self.send_attempts = 0

    async def send(
        self,
        content: str | None = None,
        reference=None,
        mention_author=False,
        view=None,
        file=None,
    ):  # noqa: ANN001
        del mention_author
        self.send_attempts += 1
        if self.send_failures > 0:
            self.send_failures -= 1
            raise TimeoutError("模拟 Discord 发送超时")
        message = FakeMessage(self, self._next_id, content)
        message.reference = reference
        message.view = view
        message.file = file
        self._next_id += 1
        self.sent_messages.append(message)
        return message


def test_turn_output_controller_keeps_progress_messages_separate_and_only_replies_once(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485470675786399764)
        source_message = FakeMessage(thread, 1485515772351746099, "请排查这个问题")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        for item_id, text in (
            ("item_progress", "根因已经定位完，收尾前我把源码行号补齐。"),
            ("item_final", "下面是最终结论和处理建议。"),
        ):
            await controller.handle_event(
                ItemStartedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": ""},
                )
            )
            await controller.handle_event(
                AgentMessageDeltaEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    delta=text,
                )
            )
            await controller.handle_event(
                ItemCompletedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": text},
                )
            )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="根因已经定位完，收尾前我把源码行号补齐。下面是最终结论和处理建议。",
                turn_status="completed",
                assistant_messages=[
                    AssistantMessageSnapshot(
                        item_id="item_progress",
                        text="根因已经定位完，收尾前我把源码行号补齐。",
                    ),
                    AssistantMessageSnapshot(
                        item_id="item_final",
                        text="下面是最终结论和处理建议。",
                    ),
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert [message.content for message in visible_messages] == [
            "根因已经定位完，收尾前我把源码行号补齐。",
            "下面是最终结论和处理建议。",
        ]
        assert visible_messages[0].reference is source_message
        assert visible_messages[1].reference is None
        assert result.message_ids == ["1000", "1001"]
        assert control_message.content == "Codex 已完成，共发送 2 条输出消息。"

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_streams_reasoning_and_clears_on_finalize(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485470675786399764)
        source_message = FakeMessage(thread, 1485515772351746099, "请继续")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")
        await controller.handle_event(
            ReasoningSummaryTextDeltaEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="reasoning_1",
                summary_index=0,
                delta="第一段思考。",
            )
        )
        await controller.handle_event(
            ReasoningSummaryTextDeltaEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="reasoning_1",
                summary_index=0,
                delta="继续补充。",
            )
        )
        assert controller._reasoning_stream is not None
        await controller._reasoning_stream.flush()

        assert len(thread.sent_messages) == 1
        reasoning_message = thread.sent_messages[0]
        assert reasoning_message.deleted is False
        assert "Codex 思考" in (reasoning_message.content or "")
        assert "第一段思考。继续补充。" in (reasoning_message.content or "")

        await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="",
                turn_status="completed",
                assistant_messages=[],
            )
        )

        assert reasoning_message.deleted is True
        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_shows_capacity_hint_when_turn_failed(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485470675786399764)
        source_message = FakeMessage(thread, 1485515772351746099, "请继续")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        capacity_error = "Selected model is at capacity. Please try a different model."
        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="",
                turn_status="failed",
                error_message=capacity_error,
                assistant_messages=[],
            )
        )

        assert result.state.value == "failed"
        assert "模型容量已满" in (control_message.content or "")
        assert "/codex model set" in (control_message.content or "")
        assert "Selected model is at capacity" in (control_message.content or "")

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_renders_and_persists_context_usage(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485470675786399764)
        source_message = FakeMessage(thread, 1485515772351746099, "继续")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")
        await controller.handle_event(
            TokenUsageUpdatedEvent(
                snapshot=TokenUsageSnapshot(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    last=TokenUsageBreakdown(
                        total_tokens=116_536,
                        input_tokens=115_000,
                        cached_input_tokens=4_000,
                        output_tokens=1_200,
                        reasoning_output_tokens=336,
                    ),
                    total=TokenUsageBreakdown(total_tokens=12_388_675),
                    model_context_window=258_400,
                )
            )
        )

        assert control_message.content == (
            "正在调用 Codex...\n"
            "上下文：116.5K / 258.4K（45%） [█████░░░░░]\n"
            "累计：12.4M tokens"
        )

        record = await TurnOutputService(db).get_by_turn_id("turn_1")
        assert record is not None
        assert record.token_usage_json is not None
        assert record.token_usage_json["last"]["total_tokens"] == 116_536

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="已完成",
                turn_status="completed",
                assistant_messages=[],
            )
        )

        assert result.state.value == "completed"
        assert control_message.content == (
            "Codex 已完成，共发送 1 条输出消息。\n"
            "上下文：116.5K / 258.4K（45%） [█████░░░░░]\n"
            "累计：12.4M tokens"
        )

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_does_not_replay_snapshots_with_different_ids(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "选1")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(
            discord_bot_token="token",
            discord_preview_mode="partial",
            discord_preview_min_initial_chars=1,
            discord_preview_throttle_ms=250,
        )
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        for item_id, text in (
            ("item_progress", "中间进度一"),
            ("item_final", "最终答案一"),
        ):
            await controller.handle_event(
                ItemStartedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": ""},
                )
            )
            await controller.handle_event(
                AgentMessageDeltaEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    delta=text,
                )
            )
            await controller.handle_event(
                ItemCompletedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": text},
                )
            )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="中间进度一最终答案一",
                turn_status="completed",
                assistant_messages=[
                    AssistantMessageSnapshot(item_id="message:0", text="中间进度一"),
                    AssistantMessageSnapshot(item_id="message:1", text="最终答案一"),
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert [message.content for message in visible_messages] == ["中间进度一", "最终答案一"]
        assert result.message_ids == ["1000", "1001"]

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_does_not_replay_final_snapshot_when_commentary_is_missing(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "这条问话会重复吗")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        for item_id, text in (
            ("item_commentary", "我先确认现场，再给你结论。"),
            ("item_final", "这是最终结论，不能被重复投递。"),
        ):
            await controller.handle_event(
                ItemStartedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": ""},
                )
            )
            await controller.handle_event(
                AgentMessageDeltaEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    delta=text,
                )
            )
            await controller.handle_event(
                ItemCompletedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": text},
                )
            )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="这是最终结论，不能被重复投递。",
                turn_status="completed",
                assistant_messages=[
                    AssistantMessageSnapshot(
                        item_id="message:0",
                        text="这是最终结论，不能被重复投递。",
                    ),
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert [message.content for message in visible_messages] == [
            "我先确认现场，再给你结论。",
            "这是最终结论，不能被重复投递。",
        ]
        assert result.message_ids == ["1000", "1001"]

        latest = await TurnOutputService(db).get_by_turn_id("turn_1")
        assert isinstance(latest, DiscordTurnOutput)
        assert latest.final_message_ids_json == ["1000", "1001"]

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_defaults_to_completed_message_blocks_without_preview_edits(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "选1")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        for item_id, text in (
            ("item_progress", "中间进度一"),
            ("item_final", "最终答案一"),
        ):
            await controller.handle_event(
                ItemStartedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": ""},
                )
            )
            await controller.handle_event(
                AgentMessageDeltaEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    delta=text,
                )
            )
            await controller.handle_event(
                ItemCompletedEvent(
                    thread_id="thr_1",
                    turn_id="turn_1",
                    item_id=item_id,
                    item_type="agentMessage",
                    item={"id": item_id, "type": "agentMessage", "text": text},
                )
            )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="中间进度一最终答案一",
                turn_status="completed",
                assistant_messages=[
                    AssistantMessageSnapshot(item_id="message:0", text="中间进度一"),
                    AssistantMessageSnapshot(item_id="message:1", text="最终答案一"),
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert [message.content for message in visible_messages] == ["中间进度一", "最终答案一"]
        assert result.message_ids == ["1000", "1001"]

        latest = await TurnOutputService(db).get_by_turn_id("turn_1")
        assert isinstance(latest, DiscordTurnOutput)
        assert latest.preview_message_ids_json == []
        assert latest.final_message_ids_json == ["1000", "1001"]

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_ignores_control_message_edit_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    async def scenario() -> None:
        monkeypatch.setattr(delivery.asyncio, "sleep", no_sleep)
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "请继续")
        control_message = FakeMessage(
            thread,
            2000,
            "正在调用 Codex...",
            edit_failures=99,
        )
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")
        await controller.handle_event(
            ItemStartedEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="item_1",
                item_type="agentMessage",
                item={"id": "item_1", "type": "agentMessage", "text": ""},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="item_1",
                item_type="agentMessage",
                item={"id": "item_1", "type": "agentMessage", "text": "最终回复"},
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="最终回复",
                turn_status="completed",
                assistant_messages=[
                    AssistantMessageSnapshot(item_id="item_1", text="最终回复")
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert [message.content for message in visible_messages] == ["最终回复"]
        assert result.message_ids == ["1000"]
        assert result.state.value == "completed"
        assert control_message.content == "正在调用 Codex..."
        assert control_message.edit_attempts >= 4

        latest = await TurnOutputService(db).get_by_turn_id("turn_1")
        assert isinstance(latest, DiscordTurnOutput)
        assert latest.state.value == "completed"
        assert latest.final_message_ids_json == ["1000"]

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_marks_delivery_failed_when_final_text_send_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    async def scenario() -> None:
        monkeypatch.setattr(delivery.asyncio, "sleep", no_sleep)
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        thread = FakeThread(1485515511394734221, send_failures=99)
        source_message = FakeMessage(thread, 1485523988435173466, "请继续")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="最终回复",
                turn_status="completed",
            )
        )

        assert thread.sent_messages == []
        assert thread.send_attempts == 4
        assert result.message_ids == []
        assert result.last_message_id == "2000"
        assert result.state.value == "delivery_failed"
        assert control_message.content == "Codex 已完成，但 Discord 输出投递失败，已保留 0 条输出消息。"

        latest = await TurnOutputService(db).get_by_turn_id("turn_1")
        assert isinstance(latest, DiscordTurnOutput)
        assert latest.state.value == "delivery_failed"
        assert latest.error_text is not None
        assert "Discord 投递失败" in latest.error_text
        assert latest.final_message_ids_json == []

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_sends_image_items_and_does_not_duplicate_on_finalize(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "请展示截图")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        await controller.handle_event(
            ItemCompletedEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="img_1",
                item_type="imageView",
                item={"id": "img_1", "type": "imageView", "path": str(image_path)},
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="",
                turn_status="completed",
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].reference is source_message
        assert visible_messages[0].file.filename == 'screen.png'
        assert result.message_ids == ["1000"]
        assert control_message.content == "Codex 已完成，共发送 1 条输出消息。"

        latest = await TurnOutputService(db).get_by_turn_id("turn_1")
        assert isinstance(latest, DiscordTurnOutput)
        assert latest.final_message_ids_json == ["1000"]

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_deduplicates_same_image_path_across_different_item_ids(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "请展示截图")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="img_1",
                item_type="imageView",
                item={"id": "img_1", "type": "imageView", "path": str(image_path)},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id="thr_1",
                turn_id="turn_1",
                item_id="img_2",
                item_type="imageView",
                item={"id": "img_2", "type": "imageView", "path": str(image_path)},
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="",
                turn_status="completed",
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].file.filename == "screen.png"
        assert result.message_ids == ["1000"]

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_does_not_send_placeholder_when_only_images_exist(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, "请展示截图")
        control_message = FakeMessage(thread, 2000, "正在调用 Codex...")
        settings = Settings(discord_bot_token="token")
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
        )

        await controller.bind_turn(codex_thread_id="thr_1", turn_id="turn_1")

        result = await controller.finalize(
            TurnRunResult(
                thread_id="thr_1",
                turn_id="turn_1",
                final_text="",
                turn_status="completed",
                image_artifacts=[
                    OutputImageArtifact(
                        item_id="img_1",
                        path=str(image_path),
                        source_type="imageView",
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].content is None
        assert result.message_ids == ["1000"]
        assert control_message.content == "Codex 已完成，共发送 1 条输出消息。"

        await db.close()

    asyncio.run(scenario())



def test_turn_output_controller_sends_media_directive_image_with_caption_and_no_duplicate_on_finalize(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(tmp_path),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={
                    'id': 'item_1',
                    'type': 'agentMessage',
                    'text': f'截图如下\nMEDIA: {image_path}\n请确认',
                },
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='截图如下\n请确认',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='截图如下\n请确认')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(image_path),
                        source_type='mediaDirective',
                        parent_item_id='item_1',
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].content == '截图如下\n请确认'
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].reference is source_message
        assert result.message_ids == ['1000']

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_attaches_caption_to_media_when_finalize_handles_active_item(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        first_image_path = (tmp_path / 'first.png').resolve()
        second_image_path = (tmp_path / 'second.png').resolve()
        first_image_path.write_bytes(b'png-1')
        second_image_path.write_bytes(b'png-2')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(tmp_path),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='两张图如下',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='两张图如下')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(first_image_path),
                        source_type='mediaDirective',
                        parent_item_id='item_1',
                    ),
                    OutputImageArtifact(
                        item_id='item_1:media:1',
                        path=str(second_image_path),
                        source_type='mediaDirective',
                        parent_item_id='item_1',
                    ),
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert [message.content for message in visible_messages] == ['两张图如下', None]
        assert [message.file.filename for message in visible_messages] == ['first.png', 'second.png']
        assert visible_messages[0].reference is source_message
        assert visible_messages[1].reference is None
        assert result.message_ids == ['1000', '1001']

        await db.close()

    asyncio.run(scenario())

def test_turn_output_controller_sends_markdown_link_image_with_caption(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(tmp_path),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={
                    'id': 'item_1',
                    'type': 'agentMessage',
                    'text': f'截图如下\n[monitor-dashboard.png]({image_path})\n请确认',
                },
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='截图如下\n请确认',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='截图如下\n请确认')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(image_path),
                        source_type='markdownLink',
                        parent_item_id='item_1',
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].content == '截图如下\n请确认'
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].reference is source_message
        assert result.message_ids == ['1000']

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_keeps_caption_when_same_image_path_was_already_sent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(tmp_path),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='img_1',
                item_type='imageView',
                item={'id': 'img_1', 'type': 'imageView', 'path': str(image_path)},
            )
        )
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={
                    'id': 'item_1',
                    'type': 'agentMessage',
                    'text': f'截图如下\n[monitor-dashboard.png]({image_path})\n请确认',
                },
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='截图如下\n请确认',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='截图如下\n请确认')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(image_path),
                        source_type='markdownLink',
                        parent_item_id='item_1',
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 2
        assert visible_messages[0].content is None
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].reference is source_message
        assert visible_messages[1].content == '截图如下\n请确认'
        assert visible_messages[1].file is None
        assert result.message_ids == ['1000', '1001']

        await db.close()

    asyncio.run(scenario())



def test_turn_output_controller_sends_inline_markdown_link_image_with_caption(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        image_path = (tmp_path / 'screen.png').resolve()
        image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(tmp_path),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={
                    'id': 'item_1',
                    'type': 'agentMessage',
                    'text': f'文件在 [monitor-dashboard.png]({image_path})，请确认',
                },
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='文件在，请确认',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='文件在，请确认')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(image_path),
                        source_type='markdownLink',
                        parent_item_id='item_1',
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].content == '文件在，请确认'
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].reference is source_message
        assert result.message_ids == ['1000']

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_keeps_caption_when_recovered_image_path_was_already_sent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        workspace_dir = tmp_path / 'workspace'
        runtime_dir = tmp_path / 'runtime'
        workspace_dir.mkdir()
        runtime_dir.mkdir()

        actual_image_path = (runtime_dir / 'screen.png').resolve()
        broken_image_path = (workspace_dir / 'screen.png').resolve()
        actual_image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(workspace_dir),
            runtime_cwd=str(runtime_dir),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='img_1',
                item_type='imageView',
                item={'id': 'img_1', 'type': 'imageView', 'path': str(actual_image_path)},
            )
        )
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={
                    'id': 'item_1',
                    'type': 'agentMessage',
                    'text': f'截图如下\n[monitor-dashboard.png]({broken_image_path})\n请确认',
                },
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='截图如下\n请确认',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='截图如下\n请确认')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(broken_image_path),
                        source_type='markdownLink',
                        parent_item_id='item_1',
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 2
        assert visible_messages[0].content is None
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].reference is source_message
        assert visible_messages[1].content == '截图如下\n请确认'
        assert visible_messages[1].file is None
        assert result.message_ids == ['1000', '1001']

        await db.close()

    asyncio.run(scenario())


def test_turn_output_controller_sends_recovered_workspace_markdown_link_image_with_caption(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
        db = Database(database_url)
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        workspace_dir = tmp_path / 'workspace'
        runtime_dir = tmp_path / 'runtime'
        workspace_dir.mkdir()
        runtime_dir.mkdir()

        actual_image_path = (runtime_dir / 'screen.png').resolve()
        broken_image_path = (workspace_dir / 'screen.png').resolve()
        actual_image_path.write_bytes(b'png')

        thread = FakeThread(1485515511394734221)
        source_message = FakeMessage(thread, 1485523988435173466, '请展示截图')
        control_message = FakeMessage(thread, 2000, '正在调用 Codex...')
        settings = Settings(discord_bot_token='token')
        controller = TurnOutputController(
            settings=settings,
            turn_output_service=TurnOutputService(db),
            source_message=source_message,  # type: ignore[arg-type]
            control_message=control_message,  # type: ignore[arg-type]
            workspace_cwd=str(workspace_dir),
            runtime_cwd=str(runtime_dir),
        )

        await controller.bind_turn(codex_thread_id='thr_1', turn_id='turn_1')
        await controller.handle_event(
            ItemStartedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={'id': 'item_1', 'type': 'agentMessage', 'text': ''},
            )
        )
        await controller.handle_event(
            ItemCompletedEvent(
                thread_id='thr_1',
                turn_id='turn_1',
                item_id='item_1',
                item_type='agentMessage',
                item={
                    'id': 'item_1',
                    'type': 'agentMessage',
                    'text': f'截图如下\n[monitor-dashboard.png]({broken_image_path})\n请确认',
                },
            )
        )

        result = await controller.finalize(
            TurnRunResult(
                thread_id='thr_1',
                turn_id='turn_1',
                final_text='截图如下\n请确认',
                turn_status='completed',
                assistant_messages=[
                    AssistantMessageSnapshot(item_id='item_1', text='截图如下\n请确认')
                ],
                image_artifacts=[
                    OutputImageArtifact(
                        item_id='item_1:media:0',
                        path=str(broken_image_path),
                        source_type='markdownLink',
                        parent_item_id='item_1',
                    )
                ],
            )
        )

        visible_messages = [message for message in thread.sent_messages if not message.deleted]
        assert len(visible_messages) == 1
        assert visible_messages[0].content == '截图如下\n请确认'
        assert visible_messages[0].file.filename == 'screen.png'
        assert visible_messages[0].reference is source_message
        assert result.message_ids == ['1000']

        await db.close()

    asyncio.run(scenario())
