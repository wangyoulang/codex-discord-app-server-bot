from __future__ import annotations

from dataclasses import dataclass
import re

import discord

from codex_discord_bot.codex.stream_events import AgentMessageDeltaEvent
from codex_discord_bot.codex.stream_events import CodexStreamEvent
from codex_discord_bot.codex.stream_events import ItemCompletedEvent
from codex_discord_bot.codex.stream_events import ItemStartedEvent
from codex_discord_bot.codex.stream_renderer import AssistantMessageSnapshot
from codex_discord_bot.codex.worker import TurnRunResult
from codex_discord_bot.config import Settings
from codex_discord_bot.discord.streaming.chunker import chunk_discord_text
from codex_discord_bot.discord.streaming.draft_stream import DiscordDraftStream
from codex_discord_bot.discord.streaming.preview_chunker import PreviewChunkingConfig
from codex_discord_bot.discord.streaming.preview_chunker import PreviewTextChunker
from codex_discord_bot.discord.streaming.reply_delivery import send_text_pages
from codex_discord_bot.logging import get_logger
from codex_discord_bot.persistence.enums import TurnOutputState
from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.services.turn_output_service import TurnOutputService

logger = get_logger(__name__)

_REASONING_TAG_RE = re.compile(r"</?(thinking|reasoning)>", re.IGNORECASE)


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
        provider_label: str = "Codex",
        provider: ProviderKind | str = ProviderKind.codex,
    ) -> None:
        self.settings = settings
        self.turn_output_service = turn_output_service
        self.source_message = source_message
        self.thread = source_message.channel
        self.control_message = control_message
        self.provider_label = provider_label
        self.provider = provider

        self.codex_thread_id: str | None = None
        self.turn_id: str | None = None
        self._active_agent_item: ActiveAgentItemRender | None = None
        self._finalized_agent_item_ids: set[str] = set()
        self._finalized_agent_item_texts: list[str] = []
        self._final_message_ids: list[str] = []
        self._persisted_preview_ids: list[str] = []
        self._persisted_state: TurnOutputState | None = None

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
            provider=self.provider,
            codex_thread_id=codex_thread_id,
            codex_turn_id=turn_id,
            control_message_id=str(self.control_message.id),
        )
        await self._set_state(TurnOutputState.pending)

    async def handle_event(self, event: CodexStreamEvent) -> None:
        if self.turn_id is None or getattr(event, "turn_id", None) != self.turn_id:
            return
        if isinstance(event, ItemStartedEvent):
            await self._handle_item_started(event)
            return
        if isinstance(event, AgentMessageDeltaEvent):
            await self._handle_agent_delta(event)
            return
        if isinstance(event, ItemCompletedEvent):
            await self._handle_item_completed(event)

    async def finalize(self, result: TurnRunResult) -> TurnRenderFinalizeResult:
        if self.turn_id is None:
            await self.bind_turn(codex_thread_id=result.thread_id, turn_id=result.turn_id)

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
            await self._finalize_agent_item(final_text=active_text or None)

        if result.assistant_messages:
            pending_snapshots = self._resolve_pending_snapshots(result.assistant_messages)
            for snapshot in pending_snapshots:
                if snapshot.item_id in self._finalized_agent_item_ids:
                    continue
                await self._send_snapshot_fallback(snapshot)
        elif not self._final_message_ids and result.final_text.strip():
            await self._send_text_as_new_final_messages(result.final_text.strip())

        final_state = self._map_turn_status(result.turn_status)
        await self._set_state(final_state, error_text=result.error_message)
        await self._edit_control_message(self._build_control_summary(final_state, len(self._final_message_ids)))

        return TurnRenderFinalizeResult(
            message_ids=list(self._final_message_ids),
            last_message_id=self._final_message_ids[-1] if self._final_message_ids else str(self.control_message.id),
            state=final_state,
        )

    async def fail(self, error_text: str) -> TurnRenderFinalizeResult:
        logger.error("discord.turn_output.failed", error=error_text, turn_id=self.turn_id)
        if self.turn_id is not None:
            await self._set_state(TurnOutputState.failed, error_text=error_text)
            await self.turn_output_service.set_active_agent_item(
                codex_turn_id=self.turn_id,
                active_agent_item_id=None,
            )
        await self._edit_control_message(f"{self.provider_label} 执行失败：{error_text}")

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

    async def _handle_item_started(self, event: ItemStartedEvent) -> None:
        if event.item_type == "agentMessage":
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
            await self._edit_control_message(f"{self.provider_label} 正在输出回复...")
            return

        label = {
            "reasoning": f"{self.provider_label} 正在思考...",
            "commandExecution": f"{self.provider_label} 正在执行命令...",
            "fileChange": f"{self.provider_label} 正在生成文件修改...",
            "mcpToolCall": f"{self.provider_label} 正在调用工具...",
        }.get(event.item_type)
        if label is not None:
            await self._edit_control_message(label)

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
        if event.item_type != "agentMessage":
            return
        if self._active_agent_item is None or self._active_agent_item.item_id != event.item_id:
            return
        await self._finalize_agent_item(final_text=self._extract_item_text(event.item))

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
        await self.control_message.edit(content=content)

    def _map_turn_status(self, turn_status: str) -> TurnOutputState:
        if turn_status == "interrupted":
            return TurnOutputState.interrupted
        if turn_status == "failed":
            return TurnOutputState.failed
        return TurnOutputState.completed

    def _build_control_summary(self, state: TurnOutputState, page_count: int) -> str:
        if state == TurnOutputState.completed:
            return f"{self.provider_label} 已完成，正文共 {page_count} 页。"
        if state == TurnOutputState.interrupted:
            return f"{self.provider_label} 已中断，已保留 {page_count} 页输出。"
        if state == TurnOutputState.failed:
            return f"{self.provider_label} 执行失败，已保留 {page_count} 页输出。"
        return f"{self.provider_label} 正在处理..."

    def _reply_target_for_new_messages(self) -> discord.Message | None:
        if self.settings.discord_reply_to_mode == "none":
            return None
        if self.settings.discord_reply_to_mode == "all":
            return self.source_message
        if self._final_message_ids:
            return None
        return self.source_message

    async def _finalize_agent_item(self, final_text: str | None = None) -> None:
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
        if not text:
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
            )

        self._finalized_agent_item_ids.add(active_item.item_id)
        self._finalized_agent_item_texts.append(text)
        self._final_message_ids.extend(str(message.id) for message in final_messages)
        await self.turn_output_service.set_final_message_ids(
            codex_turn_id=self.turn_id,
            final_message_ids=list(self._final_message_ids),
        )
        await self.turn_output_service.set_active_agent_item(
            codex_turn_id=self.turn_id,
            active_agent_item_id=None,
        )
        self._active_agent_item = None
        await self._set_state(TurnOutputState.pending)
        await self._edit_control_message("Codex 正在继续处理...")

    async def _send_snapshot_fallback(self, snapshot: AssistantMessageSnapshot) -> None:
        text = snapshot.text.strip()
        if not text:
            self._finalized_agent_item_ids.add(snapshot.item_id)
            return
        await self._send_text_as_new_final_messages(text)
        self._finalized_agent_item_ids.add(snapshot.item_id)
        self._finalized_agent_item_texts.append(text)

    async def _send_text_as_new_final_messages(self, text: str) -> None:
        final_messages = await send_text_pages(
            channel=self.thread,
            text=text,
            reply_to_message=self._reply_target_for_new_messages(),
            reply_to_mode=self.settings.discord_reply_to_mode,
            max_chars=2000,
            max_lines=self.settings.discord_final_max_lines_per_message,
        )
        self._final_message_ids.extend(str(message.id) for message in final_messages)
        await self.turn_output_service.set_final_message_ids(
            codex_turn_id=self.turn_id,
            final_message_ids=list(self._final_message_ids),
        )

    @staticmethod
    def _extract_item_text(item: dict) -> str:
        text = item.get("text")
        return text if isinstance(text, str) else ""

    def _resolve_pending_snapshots(
        self,
        snapshots: list[AssistantMessageSnapshot],
    ) -> list[AssistantMessageSnapshot]:
        if not self._finalized_agent_item_texts:
            return snapshots

        prefix_index = 0
        max_prefix = min(len(self._finalized_agent_item_texts), len(snapshots))
        while prefix_index < max_prefix:
            snapshot_text = snapshots[prefix_index].text.strip()
            finalized_text = self._finalized_agent_item_texts[prefix_index].strip()
            if snapshot_text != finalized_text:
                break
            prefix_index += 1
        return snapshots[prefix_index:]

    def _clean_preview_text(self, text: str) -> str:
        cleaned = _REASONING_TAG_RE.sub("", text).strip()
        if not cleaned:
            return ""
        if cleaned.startswith("Reasoning:\n") and "\n\n" not in cleaned:
            return ""
        return cleaned
