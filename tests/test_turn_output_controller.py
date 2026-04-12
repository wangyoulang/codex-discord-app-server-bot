from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.stream_renderer import OutputImageArtifact
from codex_discord_bot.codex.worker import TurnRunResult
from codex_discord_bot.config import Settings
from codex_discord_bot.discord.streaming.turn_output_controller import TurnOutputController
from codex_discord_bot.persistence.db import Database
from codex_discord_bot.persistence.models import Base
from codex_discord_bot.persistence.models import DiscordTurnOutput
from codex_discord_bot.services.turn_output_service import TurnOutputService


class FakeMessage:
    def __init__(self, channel: "FakeThread", message_id: int, content: str | None) -> None:
        self.channel = channel
        self.id = message_id
        self.content = content
        self.deleted = False
        self.reference = None
        self.view = None
        self.file = None

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

    async def send(
        self,
        content: str | None = None,
        reference=None,
        mention_author=False,
        view=None,
        file=None,
    ):  # noqa: ANN001
        del mention_author
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
