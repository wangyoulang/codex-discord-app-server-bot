from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time

import discord

from codex_discord_bot.codex.errors import build_model_at_capacity_user_message
from codex_discord_bot.codex.errors import is_model_at_capacity_error
from codex_discord_bot.codex.stream_events import ReasoningSummaryPartAddedEvent
from codex_discord_bot.codex.stream_events import ReasoningSummaryTextDeltaEvent
from codex_discord_bot.codex.stream_events import ReasoningTextDeltaEvent
from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import CodexStreamEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_events import TokenUsageUpdatedEvent
from codex_discord_bot.codex.media_directives import parse_media_directives_from_text
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.stream_renderer import OutputImageArtifact
from codex_discord_bot.codex.stream_renderer import output_images_from_items
from codex_discord_bot.codex.token_usage import TokenUsageSnapshot
from codex_discord_bot.codex.worker import TurnRunResult
from codex_discord_bot.config import Settings
from codex_discord_bot.discord.context_usage import format_context_usage_summary_lines
from codex_discord_bot.discord.streaming.chunker import chunk_discord_text
from codex_discord_bot.discord.streaming.delivery import DiscordDeliveryError
from codex_discord_bot.discord.streaming.delivery import suppress_discord_delivery_error
from codex_discord_bot.discord.streaming.draft_stream import DiscordDraftStream
from codex_discord_bot.discord.streaming.media_loader import load_outbound_image
from codex_discord_bot.discord.streaming.preview_chunker import PreviewChunkingConfig
from codex_discord_bot.discord.streaming.preview_chunker import PreviewTextChunker
from codex_discord_bot.discord.streaming.reply_delivery import send_local_image
from codex_discord_bot.discord.streaming.reply_delivery import send_text_chunks
from codex_discord_bot.discord.streaming.reply_delivery import send_text_pages
from codex_discord_bot.logging import get_logger
from codex_discord_bot.persistence.enums import TurnOutputState
from codex_discord_bot.services.turn_output_service import TurnOutputService

logger = get_logger(__name__)

_REASONING_TAG_RE = re.compile(r"</?(thinking|reasoning)>", re.IGNORECASE)


def _normalize_finalized_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


@dataclass(slots=True)
class TurnRenderFinalizeResult:
    message_ids: list[str]
    last_message_id: str | None
    state: TurnOutputState


@dataclass(slots=True)
class ActiveAgentItemRender:
    item_id: str
    raw_text: str = ""
    clean_text: str = ""
    block_preview_text: str = ""
    preview_stream: DiscordDraftStream | None = None
    preview_chunker: PreviewTextChunker | None = None


class TurnOutputController:
    def __init__(
        self,
        *,
        settings: Settings,
        turn_output_service: TurnOutputService,
        source_message: discord.Message,
        control_message: discord.Message,
        workspace_cwd: str | None = None,
        runtime_cwd: str | None = None,
    ) -> None:
        self.settings = settings
        self.turn_output_service = turn_output_service
        self.source_message = source_message
        self.thread = source_message.channel
        self.control_message = control_message
        self.workspace_cwd = workspace_cwd
        self.runtime_cwd = runtime_cwd

        self.codex_thread_id: str | None = None
        self.turn_id: str | None = None
        self._active_agent_item: ActiveAgentItemRender | None = None
        self._finalized_agent_item_ids: set[str] = set()
        self._finalized_image_item_ids: set[str] = set()
        self._finalized_image_paths: dict[str, str] = {}
        self._finalized_agent_item_texts: list[str] = []
        self._final_message_ids: list[str] = []
        self._persisted_preview_ids: list[str] = []
        self._persisted_state: TurnOutputState | None = None
        self._delivery_error_text: str | None = None
        self._reasoning_stream: DiscordDraftStream | None = None
        self._reasoning_item_id: str | None = None
        self._reasoning_summary_text: str = ""
        self._reasoning_summary_index: int | None = None
        self._reasoning_last_rendered: str = ""
        self._reasoning_muted: bool = False
        self._control_status_text = control_message.content or "Codex 正在处理..."
        self._last_control_content = control_message.content
        self._latest_token_usage: TokenUsageSnapshot | None = None
        self._last_rendered_usage_percent: int | None = None
        self._last_usage_rendered_at = 0.0

    def _build_preview_stream(self) -> DiscordDraftStream | None:
        if self.settings.discord_preview_mode == "off":
            return None
        if not isinstance(self.thread, discord.Thread):
            return None
        return DiscordDraftStream(
            channel=self.thread,
            max_chars=2000,
            throttle_ms=self.settings.discord_preview_throttle_ms,
            min_initial_chars=self.settings.discord_preview_min_initial_chars,
        )

    def _build_reasoning_stream(self) -> DiscordDraftStream | None:
        if not hasattr(self.thread, "send"):
            return None
        return DiscordDraftStream(
            channel=self.thread,
            max_chars=2000,
            throttle_ms=self.settings.discord_preview_throttle_ms,
            min_initial_chars=1,
        )

    def _format_reasoning_message(self, text: str) -> str:
        header = "Codex 思考（实时，结束后自动删除）：\n"
        body = text.strip()
        if not body:
            return ""
        max_body_chars = 2000 - len(header)
        if max_body_chars <= 0:
            return header[:2000]
        if len(body) > max_body_chars:
            body = f"…{body[-(max_body_chars - 1):]}"
        return f"{header}{body}"

    async def _update_reasoning_message(self) -> None:
        stream = self._reasoning_stream
        if stream is None:
            stream = self._build_reasoning_stream()
            if stream is None:
                return
            self._reasoning_stream = stream

        content = self._format_reasoning_message(self._reasoning_summary_text)
        if not content or content == self._reasoning_last_rendered:
            return
        self._reasoning_last_rendered = content
        await stream.update(content)

    async def _clear_reasoning_message(self) -> None:
        if self._reasoning_stream is not None:
            await self._reasoning_stream.clear()
        self._reasoning_stream = None
        self._reasoning_item_id = None
        self._reasoning_summary_text = ""
        self._reasoning_summary_index = None
        self._reasoning_last_rendered = ""

    def _build_preview_chunker(self) -> PreviewTextChunker | None:
        if self.settings.discord_preview_mode != "block":
            return None
        return PreviewTextChunker(
            PreviewChunkingConfig(
                min_chars=self.settings.discord_block_preview_min_chars,
                max_chars=self.settings.discord_block_preview_max_chars,
                break_preference=self.settings.discord_block_preview_break_preference,
            )
        )

    async def bind_turn(self, *, codex_thread_id: str, turn_id: str) -> None:
        self.codex_thread_id = codex_thread_id
        self.turn_id = turn_id
        await self.turn_output_service.start_turn(
            discord_thread_id=str(self.source_message.channel.id),
            codex_thread_id=codex_thread_id,
            codex_turn_id=turn_id,
            control_message_id=str(self.control_message.id),
        )
        await self._set_state(TurnOutputState.pending)

    async def handle_event(self, event: CodexStreamEvent) -> None:
        if self.turn_id is None or getattr(event, "turn_id", None) != self.turn_id:
            return
        try:
            if isinstance(event, ItemStartedEvent):
                await self._handle_item_started(event)
                return
            if isinstance(event, AgentMessageDeltaEvent):
                await self._handle_agent_delta(event)
                return
            if isinstance(event, ReasoningSummaryTextDeltaEvent):
                await self._handle_reasoning_summary_text_delta(event)
                return
            if isinstance(event, ReasoningSummaryPartAddedEvent):
                await self._handle_reasoning_summary_part_added(event)
                return
            if isinstance(event, ReasoningTextDeltaEvent):
                await self._handle_reasoning_text_delta(event)
                return
            if isinstance(event, ItemCompletedEvent):
                await self._handle_item_completed(event)
                return
            if isinstance(event, TokenUsageUpdatedEvent):
                await self._handle_token_usage_updated(event)
        except DiscordDeliveryError as exc:
            await self._record_delivery_failure(str(exc))

    async def finalize(self, result: TurnRunResult) -> TurnRenderFinalizeResult:
        if self.turn_id is None:
            await self.bind_turn(codex_thread_id=result.thread_id, turn_id=result.turn_id)
        try:
            remaining_image_artifacts = list(result.image_artifacts)
            if self._active_agent_item is not None:
                active_text = self._active_agent_item.clean_text.strip()
                if not active_text and result.assistant_messages:
                    fallback = next(
                        (
                            snapshot.text
                            for snapshot in result.assistant_messages
                            if snapshot.item_id == self._active_agent_item.item_id
                        ),
                        "",
                    )
                    active_text = fallback.strip()
                active_media_artifacts, remaining_image_artifacts = self._split_image_artifacts_for_parent(
                    remaining_image_artifacts,
                    self._active_agent_item.item_id,
                )
                await self._finalize_agent_item(
                    final_text=active_text or None,
                    media_artifacts=active_media_artifacts,
                )

            if result.assistant_messages:
                pending_snapshots = self._resolve_pending_snapshots(result.assistant_messages)
                for snapshot in pending_snapshots:
                    snapshot_media, remaining_image_artifacts = self._split_image_artifacts_for_parent(
                        remaining_image_artifacts,
                        snapshot.item_id,
                    )
                    if snapshot.item_id in self._finalized_agent_item_ids:
                        continue
                    await self._send_snapshot_fallback(snapshot, snapshot_media)
            elif not self._final_message_ids and result.final_text.strip():
                await self._send_text_as_new_final_messages(result.final_text.strip())

            for artifact in remaining_image_artifacts:
                await self._send_image_artifact_if_needed(artifact)
        except DiscordDeliveryError as exc:
            await self._record_delivery_failure(str(exc))
        finally:
            await self._clear_reasoning_message()

        final_state = self._map_turn_status(result.turn_status)
        final_error_text = result.error_message
        if self._delivery_error_text is not None and final_state == TurnOutputState.completed:
            final_state = TurnOutputState.delivery_failed
            final_error_text = self._delivery_error_text
        await self._set_state(final_state, error_text=final_error_text)
        status_text = self._build_control_summary(final_state, len(self._final_message_ids))
        if final_state == TurnOutputState.failed and is_model_at_capacity_error(final_error_text or ""):
            status_text = build_model_at_capacity_user_message(final_error_text or "")
        await self._edit_control_message(status_text)

        return TurnRenderFinalizeResult(
            message_ids=list(self._final_message_ids),
            last_message_id=self._final_message_ids[-1] if self._final_message_ids else str(self.control_message.id),
            state=final_state,
        )

    async def fail(self, error_text: str) -> TurnRenderFinalizeResult:
        logger.error("discord.turn_output.failed", error=error_text, turn_id=self.turn_id)
        await self._clear_reasoning_message()
        if self.turn_id is not None:
            await self._set_state(TurnOutputState.failed, error_text=error_text)
            await self.turn_output_service.set_active_agent_item(
                codex_turn_id=self.turn_id,
                active_agent_item_id=None,
            )
        if is_model_at_capacity_error(error_text):
            await self._edit_control_message(build_model_at_capacity_user_message(error_text))
        else:
            await self._edit_control_message(f"Codex 执行失败：{error_text}")

        last_message_id = str(self.control_message.id)
        if self._active_agent_item is not None and self._active_agent_item.preview_stream is not None:
            messages = self._active_agent_item.preview_stream.messages
            if messages:
                last_message_id = str(messages[-1].id)
            await self._sync_preview_ids(self._active_agent_item.preview_stream)

        return TurnRenderFinalizeResult(
            message_ids=[last_message_id],
            last_message_id=last_message_id,
            state=TurnOutputState.failed,
        )

    async def delivery_failed(self, error_text: str) -> TurnRenderFinalizeResult:
        await self._clear_reasoning_message()
        await self._record_delivery_failure(error_text)
        await self._edit_control_message(f"Codex 已完成，但 Discord 输出投递失败：{error_text}")
        return TurnRenderFinalizeResult(
            message_ids=list(self._final_message_ids),
            last_message_id=(
                self._final_message_ids[-1] if self._final_message_ids else str(self.control_message.id)
            ),
            state=TurnOutputState.delivery_failed,
        )

    async def mark_long_running(
        self,
        *,
        elapsed_seconds: float,
        idle_seconds: float,
        active_item_type: str | None,
    ) -> None:
        item_label = {
            "reasoning": "思考",
            "commandExecution": "执行命令",
            "fileChange": "生成文件修改",
            "mcpToolCall": "调用工具",
            "agentMessage": "输出回复",
        }.get(active_item_type, "处理")
        await self._edit_control_message(
            f"Codex 已运行约 {elapsed_seconds:.0f} 秒，当前仍在{item_label}。"
            f"最近 {idle_seconds:.0f} 秒内仍有进展检测，长任务会继续等待；"
            "如需停止，请点击“打断”。"
        )

    async def _handle_item_started(self, event: ItemStartedEvent) -> None:
        if event.item_type == "agentMessage":
            if self._reasoning_stream is not None:
                await self._reasoning_stream.flush()
            self._reasoning_muted = True
            if self._active_agent_item is not None:
                await self._finalize_agent_item()

            self._active_agent_item = ActiveAgentItemRender(
                item_id=event.item_id,
                preview_stream=self._build_preview_stream(),
                preview_chunker=self._build_preview_chunker(),
            )
            await self.turn_output_service.set_active_agent_item(
                codex_turn_id=self.turn_id,
                active_agent_item_id=event.item_id,
            )
            await self._set_state(TurnOutputState.previewing)
            await self._edit_control_message("Codex 正在输出回复...")
            return

        label = {
            "reasoning": "Codex 正在思考...",
            "commandExecution": "Codex 正在执行命令...",
            "fileChange": "Codex 正在生成文件修改...",
            "mcpToolCall": "Codex 正在调用工具...",
        }.get(event.item_type)
        if label is not None:
            await self._edit_control_message(label)

    async def _handle_reasoning_summary_text_delta(
        self,
        event: ReasoningSummaryTextDeltaEvent,
    ) -> None:
        if self._reasoning_muted:
            return
        if self._reasoning_item_id is None:
            self._reasoning_item_id = event.item_id
        if self._reasoning_item_id != event.item_id:
            await self._clear_reasoning_message()
            self._reasoning_item_id = event.item_id

        if self._reasoning_summary_index is None:
            self._reasoning_summary_index = event.summary_index
        if self._reasoning_summary_index != event.summary_index:
            self._reasoning_summary_text = f"{self._reasoning_summary_text.rstrip()}\n\n"
            self._reasoning_summary_index = event.summary_index

        self._reasoning_summary_text = f"{self._reasoning_summary_text}{event.delta}"
        await self._update_reasoning_message()

    async def _handle_reasoning_summary_part_added(
        self,
        event: ReasoningSummaryPartAddedEvent,
    ) -> None:
        if self._reasoning_muted:
            return
        if self._reasoning_item_id is None:
            self._reasoning_item_id = event.item_id
        if self._reasoning_item_id != event.item_id:
            await self._clear_reasoning_message()
            self._reasoning_item_id = event.item_id

        if self._reasoning_summary_index is None:
            self._reasoning_summary_index = event.summary_index
            return
        if self._reasoning_summary_index != event.summary_index:
            self._reasoning_summary_text = f"{self._reasoning_summary_text.rstrip()}\n\n"
            self._reasoning_summary_index = event.summary_index

    async def _handle_reasoning_text_delta(
        self,
        _event: ReasoningTextDeltaEvent,
    ) -> None:
        # 原始思考（raw CoT）默认不展示；如果需要，可改为仅展示 summary 或接入单独开关。
        return

    async def _handle_agent_delta(self, event: AgentMessageDeltaEvent) -> None:
        if self._active_agent_item is None or self._active_agent_item.item_id != event.item_id:
            return

        raw_text = f"{self._active_agent_item.raw_text}{event.delta}"
        cleaned_text = self._clean_preview_text(raw_text)
        previous_cleaned = self._active_agent_item.clean_text

        self._active_agent_item.raw_text = raw_text
        self._active_agent_item.clean_text = cleaned_text
        if not cleaned_text or cleaned_text == previous_cleaned:
            return

        preview_stream = self._active_agent_item.preview_stream
        if preview_stream is None:
            return

        if self.settings.discord_preview_mode == "partial":
            if previous_cleaned and previous_cleaned.startswith(cleaned_text) and len(cleaned_text) < len(
                previous_cleaned
            ):
                return
            await preview_stream.update(cleaned_text)
            await self._sync_preview_ids(preview_stream)
            return

        delta = cleaned_text
        if cleaned_text.startswith(previous_cleaned):
            delta = cleaned_text[len(previous_cleaned) :]
        else:
            if self._active_agent_item.preview_chunker is not None:
                self._active_agent_item.preview_chunker.reset()
            self._active_agent_item.block_preview_text = ""
        if not delta:
            return

        if self._active_agent_item.preview_chunker is None:
            self._active_agent_item.block_preview_text = cleaned_text
            await preview_stream.update(self._active_agent_item.block_preview_text)
            await self._sync_preview_ids(preview_stream)
            return

        self._active_agent_item.preview_chunker.append(delta)
        for chunk in self._active_agent_item.preview_chunker.drain(force=False):
            self._active_agent_item.block_preview_text += chunk
            await preview_stream.update(self._active_agent_item.block_preview_text)
        await self._sync_preview_ids(preview_stream)

    async def _handle_item_completed(self, event: ItemCompletedEvent) -> None:
        if event.item_type == "reasoning":
            await self._handle_reasoning_completed(event)
            return
        if event.item_type == "agentMessage":
            if self._active_agent_item is None or self._active_agent_item.item_id != event.item_id:
                return
            final_text = self._extract_item_text(event.item)
            media_artifacts: list[OutputImageArtifact] = []
            if self.settings.discord_media_directive_enabled and final_text:
                parsed = parse_media_directives_from_text(
                    final_text,
                    item_id=event.item_id,
                    workspace_cwd=self.workspace_cwd,
                )
                final_text = parsed.text
                media_artifacts = parsed.media_artifacts
            await self._finalize_agent_item(
                final_text=final_text,
                media_artifacts=media_artifacts,
            )
            return

        artifact = self._extract_image_artifact(event.item)
        if artifact is None:
            return
        await self._send_image_artifact_if_needed(artifact)

    async def _handle_reasoning_completed(self, event: ItemCompletedEvent) -> None:
        summary_text = self._extract_reasoning_summary_text(event.item)
        if summary_text is None:
            return

        if self._reasoning_item_id is None:
            self._reasoning_item_id = event.item_id
        if self._reasoning_item_id != event.item_id:
            await self._clear_reasoning_message()
            self._reasoning_item_id = event.item_id

        self._reasoning_summary_text = summary_text
        self._reasoning_summary_index = None
        await self._update_reasoning_message()
        if self._reasoning_stream is not None:
            await self._reasoning_stream.flush()

    def _extract_reasoning_summary_text(self, item: dict) -> str | None:
        summary = item.get("summary")
        if not isinstance(summary, list):
            return None
        chunks = [chunk.strip() for chunk in summary if isinstance(chunk, str) and chunk.strip()]
        if not chunks:
            return None
        return "\n\n".join(chunks).strip()

    async def _handle_token_usage_updated(self, event: TokenUsageUpdatedEvent) -> None:
        self._latest_token_usage = event.snapshot
        if self.turn_id is not None:
            await self.turn_output_service.set_token_usage(
                codex_turn_id=self.turn_id,
                token_usage=event.snapshot.to_dict(),
            )
        if self._should_render_usage_update(event.snapshot):
            await self._render_control_message(force=False)

    async def _sync_preview_ids(self, preview_stream: DiscordDraftStream | None) -> None:
        if self.turn_id is None:
            return
        preview_ids = [str(message.id) for message in preview_stream.messages] if preview_stream is not None else []
        if preview_ids == self._persisted_preview_ids:
            return
        await self.turn_output_service.set_preview_message_ids(
            codex_turn_id=self.turn_id,
            preview_message_ids=preview_ids,
        )
        self._persisted_preview_ids = preview_ids

    async def _set_state(self, state: TurnOutputState, error_text: str | None = None) -> None:
        if self.turn_id is None:
            return
        if self._persisted_state == state and error_text is None:
            return
        await self.turn_output_service.set_state(
            codex_turn_id=self.turn_id,
            state=state,
            error_text=error_text,
        )
        self._persisted_state = state

    async def _edit_control_message(self, content: str) -> None:
        self._control_status_text = content
        await self._render_control_message(force=True)

    async def _render_control_message(self, *, force: bool) -> None:
        content = self._build_control_message_content(self._control_status_text)
        if content == self._last_control_content:
            return
        if not force and not self._should_render_usage_update(self._latest_token_usage):
            return
        await suppress_discord_delivery_error(
            lambda: self.control_message.edit(content=content),
            operation_name="discord.control_message.edit",
        )
        self._last_control_content = content
        self._last_usage_rendered_at = time.monotonic()
        self._last_rendered_usage_percent = self._usage_percent_bucket(self._latest_token_usage)

    def _build_control_message_content(self, status_text: str) -> str:
        usage_lines = format_context_usage_summary_lines(self._latest_token_usage)
        if not usage_lines:
            return status_text
        return "\n".join([status_text, *usage_lines])

    def _should_render_usage_update(self, snapshot: TokenUsageSnapshot | None) -> bool:
        if snapshot is None:
            return False
        current_percent = self._usage_percent_bucket(snapshot)
        if self._last_rendered_usage_percent is None:
            return True
        if current_percent is not None and abs(current_percent - self._last_rendered_usage_percent) >= 1:
            return True
        return time.monotonic() - self._last_usage_rendered_at >= 10

    def _usage_percent_bucket(self, snapshot: TokenUsageSnapshot | None) -> int | None:
        if snapshot is None or snapshot.context_ratio is None:
            return None
        return round(snapshot.context_ratio * 100)

    def _map_turn_status(self, turn_status: str) -> TurnOutputState:
        if turn_status == "interrupted":
            return TurnOutputState.interrupted
        if turn_status == "failed":
            return TurnOutputState.failed
        return TurnOutputState.completed

    def _build_control_summary(self, state: TurnOutputState, page_count: int) -> str:
        if state == TurnOutputState.completed:
            return f"Codex 已完成，共发送 {page_count} 条输出消息。"
        if state == TurnOutputState.delivery_failed:
            return f"Codex 已完成，但 Discord 输出投递失败，已保留 {page_count} 条输出消息。"
        if state == TurnOutputState.interrupted:
            return f"Codex 已中断，已保留 {page_count} 条输出消息。"
        if state == TurnOutputState.failed:
            return f"Codex 执行失败，已保留 {page_count} 条输出消息。"
        return "Codex 正在处理..."

    async def _record_delivery_failure(self, error_text: str) -> None:
        if self._delivery_error_text is None:
            self._delivery_error_text = error_text
        logger.warning(
            "discord.turn_output.delivery_failed",
            turn_id=self.turn_id,
            error=error_text,
        )
        if self.turn_id is not None:
            await self._set_state(TurnOutputState.delivery_failed, error_text=error_text)

    def _reply_target_for_new_messages(self) -> discord.Message | None:
        if self.settings.discord_reply_to_mode == "none":
            return None
        if self.settings.discord_reply_to_mode == "all":
            return self.source_message
        if self._final_message_ids:
            return None
        return self.source_message

    async def _finalize_agent_item(
        self,
        final_text: str | None = None,
        media_artifacts: list[OutputImageArtifact] | None = None,
    ) -> None:
        active_item = self._active_agent_item
        if active_item is None:
            return

        if active_item.preview_chunker is not None and active_item.preview_chunker.has_buffered():
            for chunk in active_item.preview_chunker.drain(force=True):
                active_item.block_preview_text += chunk
                if active_item.preview_stream is not None:
                    await active_item.preview_stream.update(active_item.block_preview_text)

        preview_stream = active_item.preview_stream
        if preview_stream is not None:
            await preview_stream.stop()
            await self._sync_preview_ids(preview_stream)

        text = (final_text or active_item.clean_text).strip()
        item_media_artifacts = media_artifacts or []
        if not text and not item_media_artifacts:
            if preview_stream is not None and preview_stream.messages:
                await preview_stream.clear()
                await self._sync_preview_ids(None)
            self._finalized_agent_item_ids.add(active_item.item_id)
            self._active_agent_item = None
            await self.turn_output_service.set_active_agent_item(
                codex_turn_id=self.turn_id,
                active_agent_item_id=None,
            )
            return

        await self._set_state(TurnOutputState.finalizing)
        if item_media_artifacts:
            if preview_stream is not None and preview_stream.messages:
                await preview_stream.clear()
                await self._sync_preview_ids(None)
            await self._send_text_with_media_as_new_final_messages(text, item_media_artifacts)
        else:
            final_chunks = chunk_discord_text(
                text,
                max_chars=2000,
                max_lines=self.settings.discord_final_max_lines_per_message,
            )
            if not final_chunks:
                final_chunks = [text]

            final_messages: list[discord.Message]
            can_reuse_preview = (
                preview_stream is not None
                and len(final_chunks) == 1
                and len(preview_stream.messages) == 1
                and preview_stream.current_message is not None
            )
            if can_reuse_preview:
                current_message = preview_stream.current_message
                assert current_message is not None
                await current_message.edit(content=final_chunks[0])
                final_messages = [current_message]
            else:
                if preview_stream is not None and preview_stream.messages:
                    await preview_stream.clear()
                    await self._sync_preview_ids(None)
                final_messages = await send_text_pages(
                    channel=self.thread,
                    text=text,
                    reply_to_message=self._reply_target_for_new_messages(),
                    reply_to_mode=self.settings.discord_reply_to_mode,
                    max_chars=2000,
                    max_lines=self.settings.discord_final_max_lines_per_message,
                    start_index=len(self._final_message_ids),
                )

            self._final_message_ids.extend(str(message.id) for message in final_messages)
            await self.turn_output_service.set_final_message_ids(
                codex_turn_id=self.turn_id,
                final_message_ids=list(self._final_message_ids),
            )

        self._finalized_agent_item_ids.add(active_item.item_id)
        if text:
            self._finalized_agent_item_texts.append(text)
        await self.turn_output_service.set_active_agent_item(
            codex_turn_id=self.turn_id,
            active_agent_item_id=None,
        )
        self._active_agent_item = None
        await self._set_state(TurnOutputState.pending)
        await self._edit_control_message("Codex 正在继续处理...")

    async def _send_snapshot_fallback(
        self,
        snapshot: AssistantMessageSnapshot,
        media_artifacts: list[OutputImageArtifact],
    ) -> None:
        text = snapshot.text.strip()
        if not text and not media_artifacts:
            self._finalized_agent_item_ids.add(snapshot.item_id)
            return
        if media_artifacts:
            await self._send_text_with_media_as_new_final_messages(text, media_artifacts)
        elif text:
            await self._send_text_as_new_final_messages(text)
        self._finalized_agent_item_ids.add(snapshot.item_id)
        if text:
            self._finalized_agent_item_texts.append(text)

    async def _send_text_as_new_final_messages(self, text: str) -> None:
        final_messages = await send_text_pages(
            channel=self.thread,
            text=text,
            reply_to_message=self._reply_target_for_new_messages(),
            reply_to_mode=self.settings.discord_reply_to_mode,
            max_chars=2000,
            max_lines=self.settings.discord_final_max_lines_per_message,
            start_index=len(self._final_message_ids),
        )
        self._final_message_ids.extend(str(message.id) for message in final_messages)
        await self.turn_output_service.set_final_message_ids(
            codex_turn_id=self.turn_id,
            final_message_ids=list(self._final_message_ids),
        )

    async def _send_text_chunks_as_new_final_messages(self, chunks: list[str]) -> None:
        if not chunks:
            return
        final_messages = await send_text_chunks(
            channel=self.thread,
            chunks=chunks,
            reply_to_message=self._reply_target_for_new_messages(),
            reply_to_mode=self.settings.discord_reply_to_mode,
            start_index=len(self._final_message_ids),
        )
        self._final_message_ids.extend(str(message.id) for message in final_messages)
        await self.turn_output_service.set_final_message_ids(
            codex_turn_id=self.turn_id,
            final_message_ids=list(self._final_message_ids),
        )

    async def _send_text_with_media_as_new_final_messages(
        self,
        text: str,
        media_artifacts: list[OutputImageArtifact],
    ) -> None:
        if not media_artifacts:
            if text:
                await self._send_text_as_new_final_messages(text)
            return

        text_chunks = chunk_discord_text(
            text,
            max_chars=2000,
            max_lines=self.settings.discord_final_max_lines_per_message,
        )
        if not text_chunks and text:
            text_chunks = [text]

        first_caption = text_chunks[0] if text_chunks else None
        remaining_text_chunks = text_chunks[1:] if len(text_chunks) > 1 else []
        first_sent = await self._send_image_artifact_if_needed(
            media_artifacts[0],
            content=first_caption,
        )
        if not first_sent and first_caption:
            remaining_text_chunks = [first_caption, *remaining_text_chunks]

        for artifact in media_artifacts[1:]:
            await self._send_image_artifact_if_needed(artifact)

        await self._send_text_chunks_as_new_final_messages(remaining_text_chunks)

    async def _send_image_artifact_if_needed(
        self,
        artifact: OutputImageArtifact,
        *,
        content: str | None = None,
    ) -> bool:
        if artifact.item_id in self._finalized_image_item_ids:
            return False

        try:
            loaded_image = load_outbound_image(
                artifact.path,
                max_bytes=self.settings.discord_outbound_image_max_bytes,
                workspace_cwd=self.workspace_cwd,
                runtime_cwd=self.runtime_cwd,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning(
                "discord.turn_output.image_missing",
                turn_id=self.turn_id,
                item_id=artifact.item_id,
                source_type=artifact.source_type,
                path=str(artifact.path),
                error=str(exc),
            )
            return False

        normalized_path = self._normalize_image_path(str(loaded_image.path))
        existing_message_id = self._finalized_image_paths.get(normalized_path)
        if existing_message_id is not None:
            self._finalized_image_item_ids.add(artifact.item_id)
            logger.info(
                "discord.turn_output.image_deduplicated",
                turn_id=self.turn_id,
                item_id=artifact.item_id,
                source_type=artifact.source_type,
                path=normalized_path,
                existing_message_id=existing_message_id,
            )
            return False

        try:
            message = await send_local_image(
                channel=self.thread,
                image_path=loaded_image.path,
                reply_to_message=self._reply_target_for_new_messages(),
                reply_to_mode=self.settings.discord_reply_to_mode,
                reply_index=len(self._final_message_ids),
                content=content,
            )
        except (OSError, DiscordDeliveryError, discord.HTTPException) as exc:
            await self._record_delivery_failure(str(exc))
            logger.warning(
                "discord.turn_output.image_send_failed",
                turn_id=self.turn_id,
                item_id=artifact.item_id,
                source_type=artifact.source_type,
                path=str(artifact.path),
                error=str(exc),
            )
            return False

        self._finalized_image_item_ids.add(artifact.item_id)
        self._finalized_image_paths[normalized_path] = str(message.id)
        self._final_message_ids.append(str(message.id))
        await self.turn_output_service.set_final_message_ids(
            codex_turn_id=self.turn_id,
            final_message_ids=list(self._final_message_ids),
        )
        return True

    @staticmethod
    def _normalize_image_path(path: str) -> str:
        return str(Path(path).resolve(strict=False))

    @staticmethod
    def _extract_item_text(item: dict) -> str:
        text = item.get("text")
        return text if isinstance(text, str) else ""

    @staticmethod
    def _extract_image_artifact(item: dict) -> OutputImageArtifact | None:
        artifacts = output_images_from_items([item])
        if not artifacts:
            return None
        return artifacts[0]

    @staticmethod
    def _split_image_artifacts_for_parent(
        artifacts: list[OutputImageArtifact],
        parent_item_id: str,
    ) -> tuple[list[OutputImageArtifact], list[OutputImageArtifact]]:
        matched: list[OutputImageArtifact] = []
        remaining: list[OutputImageArtifact] = []
        for artifact in artifacts:
            if artifact.parent_item_id == parent_item_id:
                matched.append(artifact)
            else:
                remaining.append(artifact)
        return matched, remaining

    def _resolve_pending_snapshots(
        self,
        snapshots: list[AssistantMessageSnapshot],
    ) -> list[AssistantMessageSnapshot]:
        if not self._finalized_agent_item_texts:
            return snapshots

        prefix_index = 0
        max_prefix = min(len(self._finalized_agent_item_texts), len(snapshots))
        while prefix_index < max_prefix:
            snapshot_text = _normalize_finalized_text(snapshots[prefix_index].text)
            finalized_text = _normalize_finalized_text(self._finalized_agent_item_texts[prefix_index])
            if snapshot_text != finalized_text:
                break
            prefix_index += 1
        pending_snapshots = snapshots[prefix_index:]
        finalized_texts = {
            normalized
            for text in self._finalized_agent_item_texts
            if (normalized := _normalize_finalized_text(text))
        }
        return [
            snapshot
            for snapshot in pending_snapshots
            if _normalize_finalized_text(snapshot.text) not in finalized_texts
        ]

    def _clean_preview_text(self, text: str) -> str:
        cleaned = _REASONING_TAG_RE.sub("", text).strip()
        if not cleaned:
            return ""
        if cleaned.startswith("Reasoning:\n") and "\n\n" not in cleaned:
            return ""
        return cleaned
