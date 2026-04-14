from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import codex_discord_bot.discord.commands.session as session_commands
from codex_discord_bot.persistence.enums import SessionStatus


class FakeThread:
    def __init__(self, thread_id: str, *, parent_id: str = "forum_1", guild_id: str = "guild_1") -> None:
        self.id = thread_id
        self.parent_id = int(parent_id.removeprefix("forum_")) if parent_id.startswith("forum_") else 1
        self.guild = SimpleNamespace(
            id=int(guild_id.removeprefix("guild_")) if guild_id.startswith("guild_") else 1
        )


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.deferred = False
        self.deferred_ephemeral = False
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = True
        self.deferred_ephemeral = ephemeral
        self._done = True

    async def send_message(self, message: str, ephemeral: bool = False) -> None:
        self.messages.append({"message": message, "ephemeral": ephemeral})
        self._done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, message: str, ephemeral: bool = False) -> None:
        self.messages.append({"message": message, "ephemeral": ephemeral})


class FakeInteraction:
    def __init__(self, channel: FakeThread) -> None:
        self.channel = channel
        self.guild = SimpleNamespace(id=123456)
        self.user = SimpleNamespace(id=654321)
        self.namespace = SimpleNamespace()
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeLease:
    def __init__(self, browser: object) -> None:
        self.browser = browser

    async def __aenter__(self) -> object:
        return self.browser

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeWorkerPool:
    def __init__(self, browser: object) -> None:
        self.browser = browser
        self.lease_keys: list[str] = []

    def has_worker(self, _thread_id: str) -> bool:
        return False

    def get_worker(self, _thread_id: str) -> None:
        return None

    def is_busy(self, _thread_id: str) -> bool:
        return False

    def lease(self, key: str) -> FakeLease:
        self.lease_keys.append(key)
        return FakeLease(self.browser)


class FakeCodexThreadService:
    def __init__(
        self,
        *,
        source_label: str = "cli",
        preview: str = "测试预览",
        list_records: list[SimpleNamespace] | None = None,
    ) -> None:
        self.source_label = source_label
        self.preview = preview
        self.list_records = list(list_records or [])
        self.sync_calls: list[dict[str, object]] = []
        self.sync_batch_calls: list[dict[str, object]] = []
        self.bind_calls: list[dict[str, str]] = []
        self.release_calls: list[dict[str, str]] = []
        self.archived_calls: list[dict[str, object]] = []

    async def sync_thread_from_payload(
        self,
        *,
        workspace_id: int,
        thread_payload: dict[str, object],
        archived: bool,
        source_override: object | None = None,
    ) -> SimpleNamespace:
        self.sync_calls.append(
            {
                "workspace_id": workspace_id,
                "thread_payload": thread_payload,
                "archived": archived,
                "source_override": source_override,
            }
        )
        return SimpleNamespace(
            source_label=self.source_label,
            preview=self.preview,
            bound_discord_thread_id=None,
        )

    async def bind_thread_to_discord(
        self,
        *,
        workspace_id: int,
        codex_thread_id: str,
        discord_thread_id: str,
    ) -> None:
        self.bind_calls.append(
            {
                "workspace_id": str(workspace_id),
                "codex_thread_id": codex_thread_id,
                "discord_thread_id": discord_thread_id,
            }
        )

    async def ensure_thread_available_for_discord(
        self,
        *,
        workspace_id: int,
        codex_thread_id: str,
        discord_thread_id: str,
    ) -> None:
        self.bind_calls.append(
            {
                "workspace_id": str(workspace_id),
                "codex_thread_id": codex_thread_id,
                "discord_thread_id": discord_thread_id,
            }
        )

    async def get_by_codex_thread_id(self, codex_thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            codex_thread_id=codex_thread_id,
            source_label=self.source_label,
            archived=False,
            preview=self.preview,
            bound_discord_thread_id="1001",
        )

    async def list_for_workspace(
        self,
        *,
        workspace_id: int,
        scope: str,
        query: str | None = None,
        archived: bool = False,
        limit: int = 10,
    ) -> list[SimpleNamespace]:
        del workspace_id
        records = [record for record in self.list_records if record.archived == archived]
        if scope == "bot":
            records = [record for record in records if record.source_label == "discord-bot"]
        if query:
            lowered = query.lower()
            records = [
                record
                for record in records
                if lowered in record.codex_thread_id.lower()
                or lowered in (record.preview or "").lower()
                or lowered in (record.source_label or "").lower()
            ]
        return records[:limit]

    async def sync_threads_from_payloads(
        self,
        *,
        workspace_id: int,
        thread_payloads: list[dict[str, object]],
        archived: bool,
    ) -> list[SimpleNamespace]:
        self.sync_batch_calls.append(
            {
                "workspace_id": workspace_id,
                "thread_payloads": thread_payloads,
                "archived": archived,
            }
        )
        records: list[SimpleNamespace] = []
        for payload in thread_payloads:
            source = payload.get("source")
            source_label = None
            if isinstance(source, str):
                source_label = source
            elif isinstance(source, dict):
                source_label = source.get("custom")
            records.append(
                SimpleNamespace(
                    codex_thread_id=payload.get("id"),
                    source_label=source_label or self.source_label,
                    archived=archived,
                    preview=payload.get("preview"),
                    bound_discord_thread_id=payload.get("bound_discord_thread_id"),
                    thread_updated_at=None,
                )
            )
        return records

    async def release_binding_if_owned(
        self,
        *,
        codex_thread_id: str,
        discord_thread_id: str,
    ) -> None:
        self.release_calls.append(
            {
                "codex_thread_id": codex_thread_id,
                "discord_thread_id": discord_thread_id,
            }
        )

    async def set_archived_state(
        self,
        *,
        codex_thread_id: str,
        archived: bool,
    ) -> None:
        self.archived_calls.append({"codex_thread_id": codex_thread_id, "archived": archived})


class FakeSessionService:
    def __init__(self) -> None:
        self.bind_calls: list[dict[str, str | None]] = []
        self.mark_ready_calls: list[str] = []
        self.mark_uninitialized_calls: list[str] = []

    async def bind_codex_thread(
        self,
        *,
        discord_thread_id: str,
        codex_thread_id: str | None,
    ) -> None:
        self.bind_calls.append(
            {
                "discord_thread_id": discord_thread_id,
                "codex_thread_id": codex_thread_id,
            }
        )

    async def mark_ready(
        self,
        *,
        discord_thread_id: str,
        last_bot_message_id: str | None = None,
    ) -> None:
        del last_bot_message_id
        self.mark_ready_calls.append(discord_thread_id)

    async def get_session_for_thread(self, _discord_thread_id: str) -> None:
        return None

    async def detach_codex_thread(self, *, discord_thread_id: str) -> None:
        self.bind_calls.append(
            {
                "discord_thread_id": discord_thread_id,
                "codex_thread_id": None,
            }
        )

    async def mark_uninitialized(self, *, discord_thread_id: str) -> None:
        self.mark_uninitialized_calls.append(discord_thread_id)


class FakeAuditService:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(self, **payload) -> None:
        self.records.append(payload)


def _find_command_callback(app_state: object, name: str):
    group = session_commands.build_group(app_state)
    for command in group.commands:
        if command.name == name:
            return command.callback
    raise AssertionError(f"未找到命令: {name}")


def _build_app_state(
    *,
    browser: object | None,
    workspace_cwd: str = "/repo/",
    current_codex_thread_id: str | None = None,
    session_status: SessionStatus | None = None,
    source_label: str = "cli",
    preview: str = "测试预览",
    list_records: list[SimpleNamespace] | None = None,
):
    workspace = SimpleNamespace(id=6, cwd=workspace_cwd)
    if session_status is None:
        session_status = (
            SessionStatus.ready if current_codex_thread_id is not None else SessionStatus.uninitialized
        )
    session = SimpleNamespace(
        codex_thread_id=current_codex_thread_id,
        active_turn_id=None,
        status=session_status,
        discord_thread_id="1001",
        last_bot_message_id=None,
    )
    route = SimpleNamespace(workspace=workspace, session=session)
    browser_obj = browser or SimpleNamespace()
    return SimpleNamespace(
        session_router=SimpleNamespace(
            ensure_route_for_thread=_async_return(route),
        ),
        worker_pool=FakeWorkerPool(browser_obj),
        codex_thread_service=FakeCodexThreadService(
            source_label=source_label,
            preview=preview,
            list_records=list_records,
        ),
        session_service=FakeSessionService(),
        audit_service=FakeAuditService(),
        turn_output_service=SimpleNamespace(get_latest_for_thread=_async_return(None)),
    )


def _async_return(value):
    async def inner(*args, **kwargs):
        del args, kwargs
        return value

    return inner


def _make_record(
    codex_thread_id: str,
    *,
    source_label: str = "cli",
    preview: str | None = "测试预览",
    archived: bool = False,
    bound_discord_thread_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        codex_thread_id=codex_thread_id,
        source_label=source_label,
        preview=preview,
        archived=archived,
        bound_discord_thread_id=bound_discord_thread_id,
        thread_updated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("/repo/", "/repo", True),
        ("/repo/./sub/..", "/repo", True),
        ("/repo//nested/", "/repo/nested", True),
        ("/repo-a", "/repo-b", False),
        ("/repo", None, False),
        (None, "/repo", False),
    ],
)
def test_same_workspace_cwd_normalizes_equivalent_paths(
    left: object, right: object, expected: bool
) -> None:
    assert session_commands._same_workspace_cwd(left, right) is expected


def test_resume_session_accepts_normalized_workspace_cwd() -> None:
    class FakeBrowser:
        async def read_thread(self, session_id: str, *, include_turns: bool) -> dict[str, object]:
            assert session_id == "thr_1"
            assert include_turns is False
            return {"id": "thr_1", "cwd": "/repo", "preview": "来自列表", "source": "cli"}

    async def scenario() -> None:
        app_state = _build_app_state(browser=FakeBrowser(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1001"))
        callback = _find_command_callback(app_state, "resume")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction, session="thr_1", scope="workspace", takeover=False)

        assert app_state.worker_pool.lease_keys == ["session-browser:6"]
        assert app_state.codex_thread_service.bind_calls == [
            {
                "workspace_id": "6",
                "codex_thread_id": "thr_1",
                "discord_thread_id": "1001",
            }
        ]
        assert app_state.session_service.bind_calls == [
            {
                "discord_thread_id": "1001",
                "codex_thread_id": "thr_1",
            }
        ]
        assert app_state.session_service.mark_ready_calls == ["1001"]
        assert interaction.response.deferred is True
        assert interaction.response.messages == []
        assert len(interaction.followup.messages) == 1
        assert "Codex 会话已恢复：`thr_1`" in interaction.followup.messages[0]["message"]

    asyncio.run(scenario())


def test_new_session_initializes_new_codex_thread() -> None:
    class FakeBrowser:
        async def start_new_thread(self, workspace) -> str:
            assert workspace.cwd == "/repo/"
            return "thr_new"

        async def read_thread(self, session_id: str, *, include_turns: bool) -> dict[str, object]:
            assert session_id == "thr_new"
            assert include_turns is False
            return {"id": "thr_new", "cwd": "/repo", "preview": "新会话", "source": {"custom": "discord-bot"}}

    async def scenario() -> None:
        app_state = _build_app_state(browser=FakeBrowser(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1005"))
        callback = _find_command_callback(app_state, "new")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction)

        assert app_state.worker_pool.lease_keys == ["1005"]
        assert app_state.session_service.bind_calls == [
            {
                "discord_thread_id": "1005",
                "codex_thread_id": "thr_new",
            }
        ]
        assert app_state.session_service.mark_ready_calls == ["1005"]
        assert app_state.codex_thread_service.sync_calls == []
        assert interaction.response.deferred is True
        assert interaction.response.messages == []
        assert len(interaction.followup.messages) == 1
        assert "Codex 会话已准备：`thr_new`" in interaction.followup.messages[0]["message"]

    asyncio.run(scenario())


def test_new_session_rejects_when_current_thread_already_initialized() -> None:
    async def scenario() -> None:
        app_state = _build_app_state(
            browser=SimpleNamespace(),
            workspace_cwd="/repo/",
            current_codex_thread_id="thr_existing",
            session_status=SessionStatus.ready,
        )
        interaction = FakeInteraction(FakeThread("1006"))
        callback = _find_command_callback(app_state, "new")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction)

        assert app_state.worker_pool.lease_keys == []
        assert app_state.session_service.bind_calls == []
        assert interaction.response.deferred is True
        assert interaction.followup.messages == [
            {
                "message": "当前线程已经初始化过 Codex 会话。如需创建新会话，请先执行 `/codex session detach` 后再重试。",
                "ephemeral": True,
            }
        ]

    asyncio.run(scenario())


def test_status_command_shows_uninitialized_session() -> None:
    async def scenario() -> None:
        app_state = _build_app_state(browser=SimpleNamespace(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1007"))
        callback = _find_command_callback(app_state, "status")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction)

        assert len(interaction.response.messages) == 1
        message = interaction.response.messages[0]["message"]
        assert "codex_thread_id: `无`" in message
        assert "status: `uninitialized`" in message

    asyncio.run(scenario())


def test_resume_session_rejects_different_workspace_cwd() -> None:
    class FakeBrowser:
        async def read_thread(self, session_id: str, *, include_turns: bool) -> dict[str, object]:
            assert session_id == "thr_2"
            assert include_turns is False
            return {"id": "thr_2", "cwd": "/other-repo", "preview": "来自列表", "source": "cli"}

    async def scenario() -> None:
        app_state = _build_app_state(browser=FakeBrowser(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1002"))
        callback = _find_command_callback(app_state, "resume")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction, session="thr_2", scope="workspace", takeover=False)

        assert app_state.codex_thread_service.bind_calls == []
        assert app_state.session_service.bind_calls == []
        assert interaction.response.deferred is True
        assert interaction.followup.messages == [
            {
                "message": "目标会话不属于当前工作区，无法恢复。",
                "ephemeral": True,
            }
        ]

    asyncio.run(scenario())


def test_unarchive_session_accepts_normalized_workspace_cwd() -> None:
    class FakeBrowser:
        async def unarchive_thread(self, session_id: str) -> dict[str, object]:
            assert session_id == "thr_3"
            return {"id": "thr_3", "cwd": "/repo", "preview": "取消归档", "source": "cli"}

    async def scenario() -> None:
        app_state = _build_app_state(browser=FakeBrowser(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1003"))
        callback = _find_command_callback(app_state, "unarchive")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction, session="thr_3", scope="workspace")

        assert app_state.worker_pool.lease_keys == ["session-browser:6"]
        assert app_state.codex_thread_service.archived_calls == [
            {"codex_thread_id": "thr_3", "archived": False}
        ]
        assert interaction.response.deferred is True
        assert interaction.response.messages == []
        assert len(interaction.followup.messages) == 1
        assert "已取消归档 Codex 会话：`thr_3`" in interaction.followup.messages[0]["message"]

    asyncio.run(scenario())


def test_unarchive_session_rejects_different_workspace_cwd() -> None:
    class FakeBrowser:
        async def unarchive_thread(self, session_id: str) -> dict[str, object]:
            assert session_id == "thr_4"
            return {"id": "thr_4", "cwd": "/elsewhere", "preview": "取消归档", "source": "cli"}

    async def scenario() -> None:
        app_state = _build_app_state(browser=FakeBrowser(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1004"))
        callback = _find_command_callback(app_state, "unarchive")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction, session="thr_4", scope="workspace")

        assert app_state.codex_thread_service.archived_calls == []
        assert interaction.response.deferred is True
        assert interaction.followup.messages == [
            {
                "message": "目标会话不属于当前工作区，无法取消归档。",
                "ephemeral": True,
            }
        ]

    asyncio.run(scenario())


def test_resume_autocomplete_uses_local_records_only() -> None:
    class ExplodingBrowser:
        async def list_threads(self, **_kwargs) -> list[dict[str, object]]:
            raise AssertionError("自动补全不应访问 app-server")

    async def scenario() -> None:
        app_state = _build_app_state(
            browser=ExplodingBrowser(),
            list_records=[
                _make_record("thr_hidden", preview=None),
                _make_record("thr_visible", preview="可以恢复的会话", bound_discord_thread_id="1001"),
            ],
        )
        interaction = FakeInteraction(FakeThread("1001"))
        interaction.namespace.scope = "workspace"
        group = session_commands.build_group(app_state)
        command = next(command for command in group.commands if command.name == "resume")
        autocomplete = command._params["session"].autocomplete

        with patch.object(session_commands.discord, "Thread", FakeThread):
            choices = await autocomplete(interaction, "")

        assert app_state.worker_pool.lease_keys == []
        assert [choice.value for choice in choices] == ["thr_visible"]

    asyncio.run(scenario())


def test_list_sessions_uses_live_results_and_filters_empty_preview() -> None:
    class FakeBrowser:
        async def list_threads(
            self,
            *,
            cwd: str,
            limit: int,
            search_term: str | None,
            archived: bool,
        ) -> list[dict[str, object]]:
            assert cwd == "/repo/"
            assert limit == 10
            assert search_term is None
            assert archived is False
            return [
                {"id": "thr_empty", "cwd": "/repo", "preview": None, "source": "cli"},
                {"id": "thr_live", "cwd": "/repo", "preview": "有效会话", "source": "discord-bot"},
            ]

    async def scenario() -> None:
        app_state = _build_app_state(browser=FakeBrowser(), workspace_cwd="/repo/")
        interaction = FakeInteraction(FakeThread("1009"))
        callback = _find_command_callback(app_state, "list")

        with patch.object(session_commands.discord, "Thread", FakeThread):
            await callback(interaction, scope="workspace", include_archived=False)

        assert app_state.worker_pool.lease_keys == ["session-browser:6"]
        assert interaction.response.deferred is True
        assert interaction.response.messages == []
        assert len(interaction.followup.messages) == 1
        message = interaction.followup.messages[0]["message"]
        assert "`thr_live`" in message
        assert "`thr_empty`" not in message

    asyncio.run(scenario())
