from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.worker import TurnRunResult
from codex_discord_bot.config import Settings
from codex_discord_bot.discord.streaming.turn_output_controller import TurnOutputController
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.models import DiscordTurnOutput
from codex_discord_bot.services.turn_output_service import TurnOutputService


class FakeMessage:
    def __init__(self, channel: "FakeThread", message_id: int, content: str) -> None:
        self.channel = channel
        self.id = message_id
        self.content = content
        self.deleted = False
        self.reference = None
        self.view = None

    async def edit(self, *, content: str, view=None) -> None:  # noqa: ANN001
        self.content = content
        self.view = view

    async def delete(self) -> None:
        self.deleted = True


class FakeThread:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent_messages: list[FakeMessage] = []
        self._next_id = 1000

    async def send(self, content: str, reference=None, mention_author=False, view=None):  # noqa: ANN001
        message = FakeMessage(self, self._next_id, content)
        message.reference = reference
        message.view = view
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
        assert control_message.content == "Codex 已完成，正文共 2 页。"

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
