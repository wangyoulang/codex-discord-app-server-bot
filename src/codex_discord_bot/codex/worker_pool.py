from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from codex_discord_bot.codex.worker import CodexWorker
from codex_discord_bot.config import Settings
from codex_discord_bot.utils.time import utc_now


@dataclass(slots=True)
class WorkerEntry:
    worker: CodexWorker
    lock: asyncio.Lock
    last_used_at: object


class WorkerPool:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._entries: dict[str, WorkerEntry] = {}
        self._manager_lock = asyncio.Lock()

    async def _get_or_create_entry(self, worker_key: str) -> WorkerEntry:
        async with self._manager_lock:
            entry = self._entries.get(worker_key)
            if entry is not None:
                return entry
            worker = CodexWorker(self.settings, worker_key=worker_key)
            entry = WorkerEntry(
                worker=worker,
                lock=asyncio.Lock(),
                last_used_at=utc_now(),
            )
            self._entries[worker_key] = entry
            return entry

    @asynccontextmanager
    async def lease(self, worker_key: str):
        entry = await self._get_or_create_entry(worker_key)
        async with entry.lock:
            entry.last_used_at = utc_now()
            yield entry.worker
            entry.last_used_at = utc_now()

    def is_busy(self, worker_key: str) -> bool:
        entry = self._entries.get(worker_key)
        return bool(entry and entry.lock.locked())

    def has_worker(self, worker_key: str) -> bool:
        return worker_key in self._entries

    def get_worker(self, worker_key: str) -> CodexWorker | None:
        entry = self._entries.get(worker_key)
        if entry is None:
            return None
        return entry.worker

    async def force_reset(self, worker_key: str) -> None:
        async with self._manager_lock:
            entry = self._entries.pop(worker_key, None)
        if entry is not None:
            await entry.worker.close()

    async def reap_idle_workers(self) -> int:
        cutoff_seconds = self.settings.worker_idle_timeout_seconds
        now = utc_now()
        to_close: list[str] = []

        async with self._manager_lock:
            for worker_key, entry in self._entries.items():
                if entry.lock.locked():
                    continue
                idle_seconds = (now - entry.last_used_at).total_seconds()
                if idle_seconds >= cutoff_seconds:
                    to_close.append(worker_key)

            closing_entries = {key: self._entries.pop(key) for key in to_close}

        for entry in closing_entries.values():
            await entry.worker.close()
        return len(to_close)

    async def close_all(self) -> None:
        async with self._manager_lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await entry.worker.close()
