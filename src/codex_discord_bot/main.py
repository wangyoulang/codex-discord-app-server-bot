from __future__ import annotations

import asyncio
import random

from codex_discord_bot.codex.session_router import SessionRouter
from codex_discord_bot.codex.worker_pool import WorkerPool
from codex_discord_bot.discord.bot import CodexDiscordBot
from codex_discord_bot.logging import configure_logging
from codex_discord_bot.logging import get_logger
from codex_discord_bot.runtime.startup import build_application_context

logger = get_logger(__name__)

# 重连配置
RECONNECT_MIN_DELAY = 5.0      # 最小重连间隔（秒）
RECONNECT_MAX_DELAY = 300.0    # 最大重连间隔（秒，5分钟）
RECONNECT_BACKOFF_FACTOR = 2.0 # 指数退避倍数
RECONNECT_JITTER = 0.1         # 随机抖动比例（避免惊群）


def _calculate_reconnect_delay(attempt: int) -> float:
    """
    计算重连延迟时间，使用指数退避 + 随机抖动

    参数attempt：当前重连尝试次数，从1开始
    返回：建议的等待秒数
    """
    delay = RECONNECT_MIN_DELAY * (RECONNECT_BACKOFF_FACTOR ** (attempt - 1))
    delay = min(delay, RECONNECT_MAX_DELAY)
    jitter = delay * RECONNECT_JITTER * (random.random() * 2 - 1)
    return delay + jitter


async def _run_bot_with_reconnect(app_state) -> None:
    """
    运行 Discord Bot，在网络断开时自动重连

    参数app_state：应用全局状态对象，包含配置和服务实例
    """
    reconnect_attempt = 0

    while True:
        bot = CodexDiscordBot(app_state)
        try:
            logger.info("discord.connecting", attempt=reconnect_attempt + 1)
            await bot.start(app_state.settings.discord_bot_token)
            # 正常关闭（非异常退出），重置重连计数
            reconnect_attempt = 0
            break
        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("discord.shutdown_requested")
            raise
        except Exception as exc:
            reconnect_attempt += 1
            delay = _calculate_reconnect_delay(reconnect_attempt)
            logger.warning(
                "discord.disconnected",
                error=str(exc),
                reconnect_attempt=reconnect_attempt,
                delay_seconds=round(delay, 1),
            )
            try:
                await bot.close()
            except Exception:
                pass
            logger.info("discord.reconnecting_in", seconds=round(delay, 1))
            await asyncio.sleep(delay)


async def amain() -> None:
    app_state = await build_application_context()
    configure_logging(app_state.settings.log_level)
    app_state.worker_pool = WorkerPool(app_state.settings)
    app_state.session_router = SessionRouter(
        app_state.workspace_service,
        app_state.session_service,
    )

    await _run_bot_with_reconnect(app_state)


def main() -> None:
    asyncio.run(amain())
