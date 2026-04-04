from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import codex_discord_bot.discord.commands.session as session_commands


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
        self._done = False

    def is_done(self) -> bool:
        return self._done

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

    def get_worker(self, _thread_id: str) -> None:
        return None

    def is_busy(self, _thread_id: str) -> bool:
        return False

    def lease(self, key: str) -> FakeLease:
        self.lease_keys.append(key)
        return FakeLease(self.browser)


class FakeCodexThreadService:
    def __init__(self, *, source_label: str = "cli", preview: str = "测试预览") -> None:
        self.source_label = source_label
        self.preview = preview
        self.sync_calls: list[dict[str, object]] = []
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
    source_label: str = "cli",
    preview: str = "测试预览",
):
    workspace = SimpleNamespace(id=6, cwd=workspace_cwd)
    session = SimpleNamespace(codex_thread_id=current_codex_thread_id, active_turn_id=None)
    route = SimpleNamespace(workspace=workspace, session=session)
    browser_obj = browser or SimpleNamespace()
    return SimpleNamespace(
        session_router=SimpleNamespace(
            ensure_route_for_thread=_async_return(route),
        ),
        worker_pool=FakeWorkerPool(browser_obj),
        codex_thread_service=FakeCodexThreadService(source_label=source_label, preview=preview),
        session_service=FakeSessionService(),
        audit_service=FakeAuditService(),
    )


def _async_return(value):
    async def inner(*args, **kwargs):
        del args, kwargs
        return value

    return inner


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
        assert len(interaction.response.messages) == 1
        assert "Codex 会话已恢复：`thr_1`" in interaction.response.messages[0]["message"]

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
        assert interaction.response.messages == [
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
        assert len(interaction.response.messages) == 1
        assert "已取消归档 Codex 会话：`thr_3`" in interaction.response.messages[0]["message"]

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
        assert interaction.response.messages == [
            {
                "message": "目标会话不属于当前工作区，无法取消归档。",
                "ephemeral": True,
            }
        ]

    asyncio.run(scenario())
