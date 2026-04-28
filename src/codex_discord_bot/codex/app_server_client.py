from __future__ import annotations

import json
import os
import queue
from dataclasses import dataclass
import subprocess
import threading
import uuid
from typing import Any
from typing import Callable


ApprovalHandler = Callable[[str, dict[str, Any] | None], dict[str, Any]]


@dataclass(slots=True)
class AppServerConfig:
    codex_bin: str | None = None
    cwd: str | None = None
    env: dict[str, str] | None = None
    client_name: str = "codex_discord_bot"
    client_title: str = "Codex Discord Bot"
    client_version: str = "0.1.0"
    experimental_api: bool = True


@dataclass(slots=True)
class Notification:
    method: str
    payload: dict[str, Any]


class AppServerClient:
    def __init__(
        self,
        *,
        config: AppServerConfig,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.config = config
        self._approval_handler = approval_handler or (lambda _method, _params: {})
        self._proc: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending_requests: dict[str, queue.Queue[dict[str, Any] | BaseException]] = {}
        self._pending_notifications: queue.Queue[Notification | BaseException] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._proc is not None:
            return

        cmd = self._build_command()
        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=self.config.cwd,
            env=env,
            bufsize=1,
        )
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="codex-app-server-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _build_command(self) -> list[str]:
        return [
            self.config.codex_bin or "codex",
            "app-server",
            "--listen",
            "stdio://",
        ]

    def close(self) -> None:
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        finally:
            self._fail_all_waiters(RuntimeError("app-server 已关闭"))

        reader_thread = self._reader_thread
        self._reader_thread = None
        if reader_thread is not None and reader_thread.is_alive():
            reader_thread.join(timeout=2)

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self.config.client_name,
                    "title": self.config.client_title,
                    "version": self.config.client_version,
                },
                "capabilities": {
                    "experimentalApi": self.config.experimental_api,
                },
            },
        )
        self.notify("initialized", {})
        return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write_message({"method": method, "params": params or {}})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        response_queue: queue.Queue[dict[str, Any] | BaseException] = queue.Queue(maxsize=1)
        with self._state_lock:
            self._pending_requests[request_id] = response_queue

        try:
            self._write_message({"id": request_id, "method": method, "params": params or {}})
        except Exception:
            with self._state_lock:
                self._pending_requests.pop(request_id, None)
            raise

        response = response_queue.get()
        if isinstance(response, BaseException):
            raise response

        error = response.get("error")
        if isinstance(error, dict):
            raise RuntimeError(error.get("message", "app-server request failed"))

        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"{method} response must be a JSON object")
        return result

    def next_notification(self) -> Notification:
        notification = self._pending_notifications.get()
        if isinstance(notification, BaseException):
            raise notification
        return notification

    def thread_start(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.request("thread/start", params)

    def thread_list(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("thread/list", params or {})

    def thread_archive(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/archive", {"threadId": thread_id})

    def thread_unarchive(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/unarchive", {"threadId": thread_id})

    def thread_resume(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {"threadId": thread_id, **params}
        return self.request("thread/resume", payload)

    def thread_read(self, thread_id: str, *, include_turns: bool) -> dict[str, Any]:
        return self.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
        )

    def turn_start(
        self,
        thread_id: str,
        input_items: list[dict[str, Any]] | dict[str, Any] | str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            **(params or {}),
            "threadId": thread_id,
            "input": self._normalize_input_items(input_items),
        }
        return self.request("turn/start", payload)

    def turn_steer(
        self,
        thread_id: str,
        input_items: list[dict[str, Any]] | dict[str, Any] | str,
        *,
        expected_turn_id: str,
    ) -> dict[str, Any]:
        payload = {
            "threadId": thread_id,
            "input": self._normalize_input_items(input_items),
            "expectedTurnId": expected_turn_id,
        }
        return self.request("turn/steer", payload)

    def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def _normalize_input_items(
        self,
        input_items: list[dict[str, Any]] | dict[str, Any] | str,
    ) -> list[dict[str, Any]]:
        if isinstance(input_items, str):
            return [{"type": "text", "text": input_items}]
        if isinstance(input_items, dict):
            return [input_items]
        return input_items

    def _write_message(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("app-server is not running")
        with self._write_lock:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()

    def _read_message(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("app-server is not running")
        line = proc.stdout.readline()
        if not line:
            stderr_tail = ""
            if proc.stderr is not None:
                stderr_tail = proc.stderr.read(2000)
            raise RuntimeError(f"app-server closed stdout: {stderr_tail}")
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError("invalid JSON-RPC payload")
        return payload

    def _reader_loop(self) -> None:
        try:
            while True:
                msg = self._read_message()

                if "method" in msg and "id" in msg:
                    self._handle_server_request(msg)
                    continue

                if "method" in msg and "id" not in msg:
                    self._pending_notifications.put(
                        Notification(method=str(msg["method"]), payload=msg.get("params") or {})
                    )
                    continue

                request_id = msg.get("id")
                if request_id is None:
                    continue

                response_queue = None
                with self._state_lock:
                    response_queue = self._pending_requests.pop(str(request_id), None)
                if response_queue is not None:
                    response_queue.put(msg)
        except BaseException as exc:
            self._fail_all_waiters(exc)

    def _handle_server_request(self, msg: dict[str, Any]) -> None:
        request_id = msg.get("id")
        if request_id is None:
            return

        try:
            response = self._approval_handler(
                str(msg["method"]),
                msg.get("params") if isinstance(msg.get("params"), dict) else None,
            )
        except Exception:
            response = {"decision": "decline"}

        self._write_message({"id": request_id, "result": response})

    def _fail_all_waiters(self, exc: BaseException) -> None:
        with self._state_lock:
            pending_requests = list(self._pending_requests.values())
            self._pending_requests.clear()

        for response_queue in pending_requests:
            response_queue.put(exc)

        self._pending_notifications.put(exc)
