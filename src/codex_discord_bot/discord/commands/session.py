from __future__ import annotations

from datetime import datetime
import os

import discord
from discord import app_commands

from codex_discord_bot.discord.handlers.interactions import send_interaction_error
from codex_discord_bot.persistence.enums import SessionStatus


def _normalize_workspace_cwd(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return os.path.normcase(os.path.normpath(value))


def _same_workspace_cwd(left: object, right: object) -> bool:
    normalized_left = _normalize_workspace_cwd(left)
    normalized_right = _normalize_workspace_cwd(right)
    if normalized_left is None or normalized_right is None:
        return False
    return normalized_left == normalized_right


def _session_is_initialized(session: object) -> bool:
    codex_thread_id = getattr(session, "codex_thread_id", None)
    status = getattr(session, "status", None)
    if not isinstance(codex_thread_id, str) or not codex_thread_id:
        return False
    return status != SessionStatus.uninitialized


def _format_codex_thread_id(session: object) -> str:
    if getattr(session, "status", None) == SessionStatus.uninitialized:
        return "无"
    codex_thread_id = getattr(session, "codex_thread_id", None)
    if isinstance(codex_thread_id, str) and codex_thread_id:
        return codex_thread_id
    return "无"


def build_group(app_state) -> app_commands.Group:
    group = app_commands.Group(name="session", description="会话管理")

    async def sync_workspace_threads(
        *,
        workspace,
        scope: str,
        search_term: str | None = None,
        limit: int = 10,
        archived: bool = False,
    ):
        browser_key = f"session-browser:{workspace.id}"
        async with app_state.worker_pool.lease(browser_key) as worker:
            thread_payloads = await worker.list_threads(
                cwd=workspace.cwd,
                limit=limit,
                search_term=search_term,
                archived=archived,
            )

        await app_state.codex_thread_service.sync_threads_from_payloads(
            workspace_id=workspace.id,
            thread_payloads=thread_payloads,
            archived=archived,
        )
        return await app_state.codex_thread_service.list_for_workspace(
            workspace_id=workspace.id,
            scope=scope,
            query=search_term,
            archived=archived,
            limit=limit,
        )

    def format_source_label(record) -> str:
        return record.source_label or "unknown"

    def format_preview(record) -> str:
        preview = (record.preview or "").strip()
        if not preview:
            return "[无预览]"
        compact = " ".join(preview.split())
        if len(compact) <= 48:
            return compact
        return f"{compact[:45]}..."

    def format_updated_at(record) -> str:
        if not isinstance(record.thread_updated_at, datetime):
            return "未知时间"
        return f"<t:{int(record.thread_updated_at.timestamp())}:R>"

    def format_binding(record, *, current_thread_id: str) -> str:
        if record.bound_discord_thread_id is None:
            return "未绑定"
        if record.bound_discord_thread_id == current_thread_id:
            return "已绑定当前线程"
        return f"已绑定线程 {record.bound_discord_thread_id}"

    def merge_records(*record_groups) -> list:
        merged: dict[str, object] = {}
        for group in record_groups:
            for record in group:
                merged[record.codex_thread_id] = record
        return list(merged.values())

    async def resume_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await session_autocomplete(
            interaction,
            current=current,
            archived=False,
        )

    async def archived_session_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await session_autocomplete(
            interaction,
            current=current,
            archived=True,
        )

    async def session_autocomplete(
        interaction: discord.Interaction,
        *,
        current: str,
        archived: bool,
    ) -> list[app_commands.Choice[str]]:
        if not isinstance(interaction.channel, discord.Thread):
            return []

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
        except ValueError:
            return []

        scope_value = getattr(interaction.namespace, "scope", "workspace") or "workspace"
        scope = scope_value.value if isinstance(scope_value, app_commands.Choice) else scope_value
        try:
            records = await sync_workspace_threads(
                workspace=route.workspace,
                scope=scope,
                search_term=current or None,
                limit=20,
                archived=archived,
            )
        except Exception:
            return []
        current_thread_id = str(interaction.channel.id)
        choices: list[app_commands.Choice[str]] = []
        for record in records[:25]:
            name = (
                f"[{format_source_label(record)}] "
                f"{format_preview(record)} | "
                f"{format_binding(record, current_thread_id=current_thread_id)}"
            )
            choices.append(app_commands.Choice(name=name[:100], value=record.codex_thread_id))
        return choices

    @group.command(name="new", description="为当前 Discord 线程初始化 Codex 会话")
    async def new_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        worker = app_state.worker_pool.get_worker(str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
            if _session_is_initialized(route.session):
                await send_interaction_error(
                    interaction,
                    "当前线程已经初始化过 Codex 会话。如需创建新会话，请先执行 `/codex session detach` 后再重试。",
                )
                return
            async with app_state.worker_pool.lease(str(interaction.channel.id)) as worker:
                codex_thread_id = await worker.start_new_thread(route.workspace)
                thread_payload = await worker.read_thread(codex_thread_id, include_turns=False)
            await app_state.session_service.bind_codex_thread(
                discord_thread_id=str(interaction.channel.id),
                codex_thread_id=codex_thread_id,
            )
            await app_state.codex_thread_service.sync_thread_from_payload(
                workspace_id=route.workspace.id,
                thread_payload=thread_payload,
                archived=False,
                source_override={"custom": "discord-bot"},
            )
            await app_state.codex_thread_service.ensure_thread_available_for_discord(
                workspace_id=route.workspace.id,
                codex_thread_id=codex_thread_id,
                discord_thread_id=str(interaction.channel.id),
            )
            await app_state.session_service.mark_ready(
                discord_thread_id=str(interaction.channel.id),
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"初始化 Codex 会话失败：{exc}")
            return

        await interaction.response.send_message(
            f"Codex 会话已准备：`{codex_thread_id}`",
            ephemeral=True,
        )

    @group.command(name="status", description="查看当前 Discord 线程的会话状态")
    async def status(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        session = route.session

        latest_output = await app_state.turn_output_service.get_latest_for_thread(
            str(interaction.channel.id)
        )
        worker_active = app_state.worker_pool.has_worker(str(interaction.channel.id))
        worker = app_state.worker_pool.get_worker(str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        codex_thread = None
        if session.codex_thread_id is not None:
            codex_thread = await app_state.codex_thread_service.get_by_codex_thread_id(session.codex_thread_id)
        await interaction.response.send_message(
            "\n".join(
                [
                    f"discord_thread_id: `{session.discord_thread_id}`",
                    f"codex_thread_id: `{_format_codex_thread_id(session)}`",
                    f"codex_source: `{codex_thread.source_label if codex_thread is not None and codex_thread.source_label else '未知'}`",
                    f"codex_archived: `{codex_thread.archived if codex_thread is not None else '未知'}`",
                    f"codex_preview: `{format_preview(codex_thread) if codex_thread is not None else '无'}`",
                    f"codex_bound_thread_id: `{codex_thread.bound_discord_thread_id if codex_thread is not None and codex_thread.bound_discord_thread_id is not None else '无'}`",
                    f"status: `{session.status.value}`",
                    f"active_turn_id: `{session.active_turn_id or '无'}`",
                    f"live_active_turn_id: `{live_active_turn.turn_id if live_active_turn is not None else '无'}`",
                    f"last_bot_message_id: `{session.last_bot_message_id or '无'}`",
                    f"output_turn_id: `{latest_output.codex_turn_id if latest_output is not None else '无'}`",
                    f"output_state: `{latest_output.state.value if latest_output is not None else '无'}`",
                    f"control_message_id: `{latest_output.control_message_id if latest_output is not None else '无'}`",
                    f"preview_count: `{len(latest_output.preview_message_ids_json or []) if latest_output is not None else 0}`",
                    f"final_page_count: `{len(latest_output.final_message_ids_json or []) if latest_output is not None else 0}`",
                    f"active_agent_item_id: `{latest_output.active_agent_item_id if latest_output is not None else '无'}`",
                    f"worker_active: `{worker_active}`",
                ]
            ),
            ephemeral=True,
        )

    @group.command(name="list", description="列出当前工作区可恢复的 Codex 会话")
    @app_commands.describe(scope="会话范围", include_archived="是否包含已归档会话")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="当前工作区", value="workspace"),
            app_commands.Choice(name="仅 Bot 来源", value="bot"),
        ]
    )
    async def list_sessions(
        interaction: discord.Interaction,
        scope: str = "workspace",
        include_archived: bool = False,
    ) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
            records = await sync_workspace_threads(
                workspace=route.workspace,
                scope=scope,
                limit=10,
                archived=False,
            )
            if include_archived:
                archived_records = await sync_workspace_threads(
                    workspace=route.workspace,
                    scope=scope,
                    limit=10,
                    archived=True,
                )
                records = merge_records(records, archived_records)
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"读取会话列表失败：{exc}")
            return

        if not records:
            await interaction.response.send_message(
                f"当前工作区下没有可恢复的 Codex 会话，scope=`{scope}`，include_archived=`{include_archived}`。",
                ephemeral=True,
            )
            return

        current_thread_id = str(interaction.channel.id)
        lines = [f"当前工作区可恢复的 Codex 会话（scope=`{scope}`，include_archived=`{include_archived}`）："]
        for record in records:
            lines.append(
                " ".join(
                    [
                        f"`{record.codex_thread_id}`",
                        f"[{format_source_label(record)}]",
                        f"[{'archived' if record.archived else 'active'}]",
                        f"{format_updated_at(record)}",
                        f"{format_binding(record, current_thread_id=current_thread_id)}",
                        f"{format_preview(record)}",
                    ]
                )
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="resume", description="在当前 Discord 线程恢复一个历史 Codex 会话")
    @app_commands.describe(session="要恢复的 Codex 会话", scope="会话范围", takeover="若已被其它 Discord 线程绑定，是否显式接管")
    @app_commands.autocomplete(session=resume_autocomplete)
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="当前工作区", value="workspace"),
            app_commands.Choice(name="仅 Bot 来源", value="bot"),
        ]
    )
    async def resume_session(
        interaction: discord.Interaction,
        session: str,
        scope: str = "workspace",
        takeover: bool = False,
    ) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        worker = app_state.worker_pool.get_worker(str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
            if _session_is_initialized(route.session) and route.session.codex_thread_id != session:
                await send_interaction_error(
                    interaction,
                    "当前线程已经绑定其它 Codex 会话。如需恢复新会话，请先执行 `/codex session detach`。",
                )
                return
            browser_key = f"session-browser:{route.workspace.id}"
            async with app_state.worker_pool.lease(browser_key) as browser:
                thread_payload = await browser.read_thread(session, include_turns=False)
            if not _same_workspace_cwd(thread_payload.get("cwd"), route.workspace.cwd):
                await send_interaction_error(interaction, "目标会话不属于当前工作区，无法恢复。")
                return

            record = await app_state.codex_thread_service.sync_thread_from_payload(
                workspace_id=route.workspace.id,
                thread_payload=thread_payload,
                archived=False,
            )
            if scope == "bot" and record.source_label != "discord-bot":
                await send_interaction_error(interaction, "scope=`bot` 仅允许恢复由 Discord bot 创建的会话。")
                return

            if record.bound_discord_thread_id and record.bound_discord_thread_id != str(interaction.channel.id):
                if not takeover:
                    await send_interaction_error(
                        interaction,
                        f"目标会话当前已绑定 Discord 线程 `{record.bound_discord_thread_id}`，如需接管请显式设置 `takeover=true`。",
                    )
                    return
                previous_session = await app_state.session_service.get_session_for_thread(record.bound_discord_thread_id)
                previous_worker = app_state.worker_pool.get_worker(record.bound_discord_thread_id)
                previous_live_turn = previous_worker.get_active_turn() if previous_worker is not None else None
                if (
                    previous_session is not None and previous_session.active_turn_id is not None
                ) or previous_live_turn is not None or app_state.worker_pool.is_busy(record.bound_discord_thread_id):
                    await send_interaction_error(interaction, "目标会话当前仍有运行中的 turn，不能接管，请先在原线程完成或打断。")
                    return
                if previous_session is not None:
                    await app_state.session_service.detach_codex_thread(
                        discord_thread_id=record.bound_discord_thread_id,
                    )
                await app_state.audit_service.record(
                    action="session_takeover",
                    guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
                    discord_thread_id=str(interaction.channel.id),
                    actor_id=str(interaction.user.id),
                    payload={
                        "codex_thread_id": session,
                        "from_discord_thread_id": record.bound_discord_thread_id,
                    },
                )

            await app_state.codex_thread_service.bind_thread_to_discord(
                workspace_id=route.workspace.id,
                codex_thread_id=session,
                discord_thread_id=str(interaction.channel.id),
            )
            if route.session.codex_thread_id and route.session.codex_thread_id != session:
                await app_state.codex_thread_service.release_binding_if_owned(
                    codex_thread_id=route.session.codex_thread_id,
                    discord_thread_id=str(interaction.channel.id),
                )
            await app_state.session_service.bind_codex_thread(
                discord_thread_id=str(interaction.channel.id),
                codex_thread_id=session,
            )
            await app_state.session_service.mark_ready(
                discord_thread_id=str(interaction.channel.id),
            )
            await app_state.audit_service.record(
                action="session_resumed",
                guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
                discord_thread_id=str(interaction.channel.id),
                actor_id=str(interaction.user.id),
                payload={
                    "codex_thread_id": session,
                    "scope": scope,
                    "takeover": takeover,
                    "source_label": record.source_label,
                },
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"恢复 Codex 会话失败：{exc}")
            return

        await interaction.response.send_message(
            "\n".join(
                [
                    f"Codex 会话已恢复：`{session}`",
                    f"来源：`{record.source_label or 'unknown'}`",
                    f"预览：`{format_preview(record)}`",
                    "后续直接发消息即可继续该会话。",
                ]
            ),
            ephemeral=True,
        )

    @group.command(name="detach", description="解除当前 Discord 线程与 Codex 会话的绑定")
    async def detach_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        worker = app_state.worker_pool.get_worker(str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        session_record = await app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session_record is None or not _session_is_initialized(session_record):
            await send_interaction_error(interaction, "当前线程没有可解绑的 Codex 会话。")
            return

        codex_thread_id = session_record.codex_thread_id
        await app_state.codex_thread_service.release_binding_if_owned(
            codex_thread_id=codex_thread_id,
            discord_thread_id=str(interaction.channel.id),
        )
        await app_state.session_service.detach_codex_thread(
            discord_thread_id=str(interaction.channel.id),
        )
        await app_state.audit_service.record(
            action="session_detached",
            guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
            discord_thread_id=str(interaction.channel.id),
            actor_id=str(interaction.user.id),
            payload={"codex_thread_id": codex_thread_id},
        )
        await interaction.response.send_message(
            f"已解除当前线程与 Codex 会话 `{codex_thread_id}` 的绑定。",
            ephemeral=True,
        )

    @group.command(name="archive", description="归档当前线程绑定的 Codex 会话")
    async def archive_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        worker = app_state.worker_pool.get_worker(str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
            if not _session_is_initialized(route.session):
                await send_interaction_error(interaction, "当前线程没有可归档的 Codex 会话。")
                return

            existing = await app_state.codex_thread_service.get_by_codex_thread_id(route.session.codex_thread_id)
            if existing is None:
                await app_state.codex_thread_service.bind_thread_to_discord(
                    workspace_id=route.workspace.id,
                    codex_thread_id=route.session.codex_thread_id,
                    discord_thread_id=str(interaction.channel.id),
                )

            browser_key = f"session-browser:{route.workspace.id}"
            async with app_state.worker_pool.lease(browser_key) as browser:
                await browser.archive_thread(route.session.codex_thread_id)

            await app_state.codex_thread_service.set_archived_state(
                codex_thread_id=route.session.codex_thread_id,
                archived=True,
            )
            await app_state.codex_thread_service.release_binding_if_owned(
                codex_thread_id=route.session.codex_thread_id,
                discord_thread_id=str(interaction.channel.id),
            )
            await app_state.session_service.detach_codex_thread(
                discord_thread_id=str(interaction.channel.id),
            )
            await app_state.audit_service.record(
                action="session_archived",
                guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
                discord_thread_id=str(interaction.channel.id),
                actor_id=str(interaction.user.id),
                payload={"codex_thread_id": route.session.codex_thread_id},
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"归档 Codex 会话失败：{exc}")
            return

        await interaction.response.send_message(
            f"已归档当前 Codex 会话：`{route.session.codex_thread_id}`。它默认不会再出现在普通会话列表里。",
            ephemeral=True,
        )

    @group.command(name="unarchive", description="取消归档一个 Codex 会话")
    @app_commands.describe(session="要取消归档的 Codex 会话", scope="会话范围")
    @app_commands.autocomplete(session=archived_session_autocomplete)
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="当前工作区", value="workspace"),
            app_commands.Choice(name="仅 Bot 来源", value="bot"),
        ]
    )
    async def unarchive_session(
        interaction: discord.Interaction,
        session: str,
        scope: str = "workspace",
    ) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        try:
            route = await app_state.session_router.ensure_route_for_thread(interaction.channel)
            browser_key = f"session-browser:{route.workspace.id}"
            async with app_state.worker_pool.lease(browser_key) as browser:
                thread_payload = await browser.unarchive_thread(session)
            if not _same_workspace_cwd(thread_payload.get("cwd"), route.workspace.cwd):
                await send_interaction_error(interaction, "目标会话不属于当前工作区，无法取消归档。")
                return
            record = await app_state.codex_thread_service.sync_thread_from_payload(
                workspace_id=route.workspace.id,
                thread_payload=thread_payload,
                archived=False,
            )
            if scope == "bot" and record.source_label != "discord-bot":
                await send_interaction_error(interaction, "scope=`bot` 仅允许操作由 Discord bot 创建的会话。")
                return
            await app_state.codex_thread_service.set_archived_state(
                codex_thread_id=session,
                archived=False,
            )
            await app_state.audit_service.record(
                action="session_unarchived",
                guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
                discord_thread_id=str(interaction.channel.id),
                actor_id=str(interaction.user.id),
                payload={"codex_thread_id": session, "scope": scope},
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"取消归档 Codex 会话失败：{exc}")
            return

        await interaction.response.send_message(
            f"已取消归档 Codex 会话：`{session}`。如需继续对话，请执行 `/codex session resume`。",
            ephemeral=True,
        )

    return group
