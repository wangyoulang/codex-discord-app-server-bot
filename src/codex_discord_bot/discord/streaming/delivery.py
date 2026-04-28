from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from typing import TypeVar

import aiohttp
import discord

from codex_discord_bot.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

RETRYABLE_HTTP_STATUSES = {500, 502, 504, 524}
DELIVERY_EXCEPTIONS = (
    TimeoutError,
    aiohttp.ClientError,
    discord.HTTPException,
    discord.RateLimited,
)


class DiscordDeliveryError(RuntimeError):
    def __init__(self, operation_name: str, cause: Exception) -> None:
        self.operation_name = operation_name
        self.cause = cause
        super().__init__(f"{operation_name} Discord 投递失败：{cause}")


def is_retryable_discord_error(exc: Exception) -> bool:
    if isinstance(exc, discord.RateLimited):
        return True
    if isinstance(exc, discord.HTTPException):
        status = getattr(exc, "status", None)
        return isinstance(status, int) and status in RETRYABLE_HTTP_STATUSES
    return isinstance(
        exc,
        (
            TimeoutError,
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
            aiohttp.ServerDisconnectedError,
        ),
    )


async def retry_discord_call(
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    attempts: int = 4,
    initial_delay: float = 1.0,
    max_delay: float = 8.0,
) -> T:
    normalized_attempts = max(1, attempts)
    delay = max(0.0, initial_delay)
    last_error: Exception | None = None

    for attempt in range(1, normalized_attempts + 1):
        try:
            return await operation()
        except DELIVERY_EXCEPTIONS as exc:
            if not is_retryable_discord_error(exc) or attempt >= normalized_attempts:
                raise DiscordDeliveryError(operation_name, exc) from exc
            last_error = exc
            sleep_seconds = _resolve_retry_delay(exc, delay)
            logger.warning(
                "discord.delivery.retry",
                operation=operation_name,
                attempt=attempt,
                attempts=normalized_attempts,
                retry_after=round(sleep_seconds, 3),
                error=str(exc),
            )
            await asyncio.sleep(sleep_seconds)
            delay = min(max_delay, delay * 2 if delay > 0 else max_delay)

    if last_error is not None:
        raise DiscordDeliveryError(operation_name, last_error) from last_error
    raise RuntimeError("Discord 投递重试流程异常结束")


async def suppress_discord_delivery_error(
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    attempts: int = 4,
) -> T | None:
    try:
        return await retry_discord_call(
            operation,
            operation_name=operation_name,
            attempts=attempts,
        )
    except DiscordDeliveryError as exc:
        logger.warning(
            "discord.delivery.suppressed",
            operation=operation_name,
            attempts=attempts,
            error=str(exc),
        )
        return None


def _resolve_retry_delay(exc: Exception, fallback_delay: float) -> float:
    if isinstance(exc, discord.RateLimited):
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, int | float):
            return max(0.0, float(retry_after))
    return fallback_delay
