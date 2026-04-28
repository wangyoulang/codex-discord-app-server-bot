from __future__ import annotations

import subprocess
import threading

from codex_discord_bot.codex.app_server_client import AppServerClient
from codex_discord_bot.codex.app_server_client import AppServerConfig


class _DummyStream:
    def close(self) -> None:
        return None

    def write(self, _value: str) -> None:
        return None

    def flush(self) -> None:
        return None

    def readline(self) -> str:
        return ""

    def read(self, _size: int | None = None) -> str:
        return ""


class _ClosingStdout(_DummyStream):
    def __init__(self, on_readline) -> None:  # noqa: ANN001
        self.on_readline = on_readline

    def readline(self) -> str:
        self.on_readline()
        return ""


class _DummyProcess:
    def __init__(self) -> None:
        self.stdin = _DummyStream()
        self.stdout = _DummyStream()
        self.stderr = _DummyStream()

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> None:
        return None

    def kill(self) -> None:
        return None


class _DummyThread:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        return None


def test_app_server_client_start_does_not_pass_removed_session_source_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _DummyProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(threading, "Thread", _DummyThread)

    client = AppServerClient(
        config=AppServerConfig(
            codex_bin="codex",
            cwd="/tmp/demo",
            env={"CODEX_HOME": "/tmp/codex-home"},
        )
    )

    client.start()

    assert captured["cmd"] == ["codex", "app-server", "--listen", "stdio://"]
    assert "--session-source" not in captured["cmd"]


def test_app_server_client_read_message_handles_close_race() -> None:
    client = AppServerClient(config=AppServerConfig(codex_bin="codex"))
    proc = _DummyProcess()
    proc.stdout = _ClosingStdout(lambda: setattr(client, "_proc", None))
    client._proc = proc

    try:
        client._read_message()
    except RuntimeError as exc:
        assert "app-server closed stdout" in str(exc)
    else:
        raise AssertionError("app-server stdout 关闭时应抛出 RuntimeError")
