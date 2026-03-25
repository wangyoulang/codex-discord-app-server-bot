from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.providers.types import provider_value


@dataclass(slots=True)
class ProviderWorkerRuntime:
    codex_pool: object
    claude_pool: object

    def _pool_for(self, provider: ProviderKind | str) -> object:
        if provider_value(provider) == ProviderKind.claude.value:
            return self.claude_pool
        return self.codex_pool

    @staticmethod
    def _worker_key(provider: ProviderKind | str, worker_key: str) -> str:
        return f"{provider_value(provider)}:{worker_key}"

    @asynccontextmanager
    async def lease(self, provider: ProviderKind | str, worker_key: str):
        pool = self._pool_for(provider)
        async with pool.lease(self._worker_key(provider, worker_key)) as worker:
            yield worker

    def is_busy(self, provider: ProviderKind | str, worker_key: str) -> bool:
        pool = self._pool_for(provider)
        return bool(pool.is_busy(self._worker_key(provider, worker_key)))

    def has_worker(self, provider: ProviderKind | str, worker_key: str) -> bool:
        pool = self._pool_for(provider)
        return bool(pool.has_worker(self._worker_key(provider, worker_key)))

    def get_worker(self, provider: ProviderKind | str, worker_key: str):
        pool = self._pool_for(provider)
        return pool.get_worker(self._worker_key(provider, worker_key))

    async def force_reset(self, provider: ProviderKind | str, worker_key: str) -> None:
        pool = self._pool_for(provider)
        await pool.force_reset(self._worker_key(provider, worker_key))

    async def reap_idle_workers(self) -> int:
        codex_closed = await self.codex_pool.reap_idle_workers()
        claude_closed = await self.claude_pool.reap_idle_workers()
        return codex_closed + claude_closed

    async def close_all(self) -> None:
        await self.codex_pool.close_all()
        await self.claude_pool.close_all()
