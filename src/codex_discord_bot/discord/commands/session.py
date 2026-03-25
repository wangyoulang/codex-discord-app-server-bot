from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands

from codex_discord_bot.discord.handlers.interactions import send_interaction_error
from codex_discord_bot.providers.types import ProviderKind
from codex_discord_bot.providers.types import provider_display_name
from codex_discord_bot.providers.types import provider_root_command


def build_group(app_state, provider: ProviderKind) -> app_commands.Group:
    provider_label = provider_display_name(provider)
    provider_root = provider_root_command(provider)
    group = app_commands.Group(name="session", description=f"{provider_label} 会话管理")

    async def sync_workspace_threads(
        *,
        workspace,
        scope: str,
        search_term: str | None = None,
        limit: int = 10,
        archived: bool = False,
    ):
        browser_key = f"session-browser:{workspace.id}"
        async with app_state.worker_pool.lease(provider, browser_key) as worker:
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
            provider=provider,
        )
        return await app_state.codex_thread_service.list_for_workspace(
            workspace_id=workspace.id,
            provider=provider,
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
        for record_group in record_groups:
            for record in record_group:
                merged[record.codex_thread_id] = record
        return list(merged.values())

    async def ensure_provider_session(interaction: discord.Interaction):
        route = await app_state.session_router.ensure_route_for_provider_thread(
            interaction.channel,
            provider=provider,
        )
        assert route.session is not None
        if route.session.provider != provider:
            raise ValueError(
                f"当前线程已绑定其它 provider，请先执行 `/{provider_root} session detach` 或使用 `/{provider_root_command(route.session.provider)} session status` 查看状态。"
            )
        return route

    async def ensure_workspace_route(interaction: discord.Interaction):
        return await app_state.session_router.ensure_route_for_thread(interaction.channel)

    async def resume_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await session_autocomplete(interaction, current=current, archived=False)

    async def archived_session_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await session_autocomplete(interaction, current=current, archived=True)

    async def session_autocomplete(
        interaction: discord.Interaction,
        *,
        current: str,
        archived: bool,
    ) -> list[app_commands.Choice[str]]:
        if not isinstance(interaction.channel, discord.Thread):
            return []

        try:
            route = await ensure_workspace_route(interaction)
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

    @group.command(name="new", description="为当前 Discord 线程初始化会话")
    async def new_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        try:
            route = await ensure_provider_session(interaction)
            if route.session.codex_thread_id and route.session.provider == provider:
                await interaction.response.send_message(
                    f"{provider_label} 会话已存在：`{route.session.codex_thread_id}`",
                    ephemeral=True,
                )
                return

            if provider == ProviderKind.codex:
                async with app_state.worker_pool.lease(provider, str(interaction.channel.id)) as worker:
                    provider_thread_id = await worker.ensure_thread(route.session, route.workspace)
                    thread_payload = await worker.read_thread(provider_thread_id, include_turns=False)
                await app_state.session_service.bind_codex_thread(
                    discord_thread_id=str(interaction.channel.id),
                    codex_thread_id=provider_thread_id,
                    provider=provider,
                )
                await app_state.codex_thread_service.sync_thread_from_payload(
                    workspace_id=route.workspace.id,
                    thread_payload=thread_payload,
                    archived=False,
                    provider=provider,
                )
                await app_state.codex_thread_service.ensure_thread_available_for_discord(
                    workspace_id=route.workspace.id,
                    codex_thread_id=provider_thread_id,
                    discord_thread_id=str(interaction.channel.id),
                    provider=provider,
                )
                message = f"{provider_label} 会话已准备：`{provider_thread_id}`"
            else:
                await app_state.session_service.mark_ready(
                    discord_thread_id=str(interaction.channel.id),
                )
                message = f"{provider_label} 会话已准备。首条消息发出后会自动生成 session_id。"
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"初始化 {provider_label} 会话失败：{exc}")
            return

        await interaction.response.send_message(message, ephemeral=True)

    @group.command(name="status", description="查看当前 Discord 线程的会话状态")
    async def status(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        session = await app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session is None:
            await interaction.response.send_message("当前线程还没有会话记录。", ephemeral=True)
            return
        if session.provider != provider:
            await interaction.response.send_message(
                f"当前线程绑定的是 `{provider_root_command(session.provider)}` 会话，请改用对应根命令查看状态。",
                ephemeral=True,
            )
            return

        latest_output = await app_state.turn_output_service.get_latest_for_thread(
            str(interaction.channel.id),
            provider=provider,
        )
        worker_active = app_state.worker_pool.has_worker(provider, str(interaction.channel.id))
        worker = app_state.worker_pool.get_worker(provider, str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        thread_record = None
        if session.codex_thread_id is not None:
            thread_record = await app_state.codex_thread_service.get_by_codex_thread_id(
                session.codex_thread_id,
                provider=provider,
            )
        await interaction.response.send_message(
            "\n".join(
                [
                    f"provider: `{session.provider.value}`",
                    f"discord_thread_id: `{session.discord_thread_id}`",
                    f"provider_thread_id: `{session.codex_thread_id or '未创建'}`",
                    f"source: `{thread_record.source_label if thread_record is not None and thread_record.source_label else '未知'}`",
                    f"archived: `{thread_record.archived if thread_record is not None else '未知'}`",
                    f"preview: `{format_preview(thread_record) if thread_record is not None else '无'}`",
                    f"bound_thread_id: `{thread_record.bound_discord_thread_id if thread_record is not None and thread_record.bound_discord_thread_id is not None else '无'}`",
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

    @group.command(name="list", description="列出当前工作区可恢复的会话")
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
            route = await ensure_workspace_route(interaction)
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
            await send_interaction_error(interaction, f"读取{provider_label}会话列表失败：{exc}")
            return

        if not records:
            await interaction.response.send_message(
                f"当前工作区下没有可恢复的 {provider_label} 会话，scope=`{scope}`，include_archived=`{include_archived}`。",
                ephemeral=True,
            )
            return

        current_thread_id = str(interaction.channel.id)
        lines = [f"当前工作区可恢复的 {provider_label} 会话（scope=`{scope}`，include_archived=`{include_archived}`）："]
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

    @group.command(name="resume", description="在当前 Discord 线程恢复一个历史会话")
    @app_commands.describe(session="要恢复的会话", scope="会话范围", takeover="若已被其它 Discord 线程绑定，是否显式接管")
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

        worker = app_state.worker_pool.get_worker(provider, str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(provider, str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        try:
            route = await ensure_provider_session(interaction)
            browser_key = f"session-browser:{route.workspace.id}"
            async with app_state.worker_pool.lease(provider, browser_key) as browser:
                thread_payload = await browser.read_thread(session, include_turns=False)
            payload_cwd = thread_payload.get("cwd")
            if isinstance(payload_cwd, str) and payload_cwd and payload_cwd != route.workspace.cwd:
                await send_interaction_error(interaction, "目标会话不属于当前工作区，无法恢复。")
                return

            record = await app_state.codex_thread_service.sync_thread_from_payload(
                workspace_id=route.workspace.id,
                thread_payload=thread_payload,
                archived=False,
                provider=provider,
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
                previous_worker = app_state.worker_pool.get_worker(provider, record.bound_discord_thread_id)
                previous_live_turn = previous_worker.get_active_turn() if previous_worker is not None else None
                if (
                    previous_session is not None and previous_session.active_turn_id is not None
                ) or previous_live_turn is not None or app_state.worker_pool.is_busy(provider, record.bound_discord_thread_id):
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
                        "provider": provider.value,
                        "provider_thread_id": session,
                        "from_discord_thread_id": record.bound_discord_thread_id,
                    },
                )

            await app_state.codex_thread_service.bind_thread_to_discord(
                workspace_id=route.workspace.id,
                codex_thread_id=session,
                discord_thread_id=str(interaction.channel.id),
                provider=provider,
            )
            if route.session.codex_thread_id and route.session.codex_thread_id != session:
                await app_state.codex_thread_service.release_binding_if_owned(
                    codex_thread_id=route.session.codex_thread_id,
                    discord_thread_id=str(interaction.channel.id),
                    provider=provider,
                )
            await app_state.session_service.bind_codex_thread(
                discord_thread_id=str(interaction.channel.id),
                codex_thread_id=session,
                provider=provider,
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
                    "provider": provider.value,
                    "provider_thread_id": session,
                    "scope": scope,
                    "takeover": takeover,
                    "source_label": record.source_label,
                },
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"恢复 {provider_label} 会话失败：{exc}")
            return

        await interaction.response.send_message(
            "\n".join(
                [
                    f"{provider_label} 会话已恢复：`{session}`",
                    f"来源：`{record.source_label or 'unknown'}`",
                    f"预览：`{format_preview(record)}`",
                    "后续直接发消息即可继续该会话。",
                ]
            ),
            ephemeral=True,
        )

    @group.command(name="detach", description="解除当前 Discord 线程与会话的绑定")
    async def detach_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        session_record = await app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session_record is None or session_record.codex_thread_id is None:
            await send_interaction_error(interaction, f"当前线程没有可解绑的 {provider_label} 会话。")
            return
        if session_record.provider != provider:
            await send_interaction_error(
                interaction,
                f"当前线程绑定的是 `{provider_root_command(session_record.provider)}` 会话，请使用对应根命令操作。",
            )
            return

        worker = app_state.worker_pool.get_worker(provider, str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(provider, str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        provider_thread_id = session_record.codex_thread_id
        await app_state.codex_thread_service.release_binding_if_owned(
            codex_thread_id=provider_thread_id,
            discord_thread_id=str(interaction.channel.id),
            provider=provider,
        )
        await app_state.session_service.detach_codex_thread(discord_thread_id=str(interaction.channel.id))
        await app_state.audit_service.record(
            action="session_detached",
            guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
            discord_thread_id=str(interaction.channel.id),
            actor_id=str(interaction.user.id),
            payload={"provider": provider.value, "provider_thread_id": provider_thread_id},
        )
        await interaction.response.send_message(
            f"已解除当前线程与 {provider_label} 会话 `{provider_thread_id}` 的绑定。",
            ephemeral=True,
        )

    @group.command(name="archive", description="归档当前线程绑定的会话")
    async def archive_session(interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.Thread):
            await send_interaction_error(interaction, "请在论坛线程中执行该命令。")
            return

        session_record = await app_state.session_service.get_session_for_thread(str(interaction.channel.id))
        if session_record is None or session_record.codex_thread_id is None:
            await send_interaction_error(interaction, f"当前线程没有可归档的 {provider_label} 会话。")
            return
        if session_record.provider != provider:
            await send_interaction_error(
                interaction,
                f"当前线程绑定的是 `{provider_root_command(session_record.provider)}` 会话，请使用对应根命令操作。",
            )
            return

        worker = app_state.worker_pool.get_worker(provider, str(interaction.channel.id))
        live_active_turn = worker.get_active_turn() if worker is not None else None
        if app_state.worker_pool.is_busy(provider, str(interaction.channel.id)) or live_active_turn is not None:
            await send_interaction_error(interaction, "当前线程存在运行中的 turn，请先等待完成或手动打断。")
            return

        try:
            route = await ensure_workspace_route(interaction)
            existing = await app_state.codex_thread_service.get_by_codex_thread_id(
                session_record.codex_thread_id,
                provider=provider,
            )
            if existing is None:
                await app_state.codex_thread_service.bind_thread_to_discord(
                    workspace_id=route.workspace.id,
                    codex_thread_id=session_record.codex_thread_id,
                    discord_thread_id=str(interaction.channel.id),
                    provider=provider,
                )

            browser_key = f"session-browser:{route.workspace.id}"
            async with app_state.worker_pool.lease(provider, browser_key) as browser:
                await browser.archive_thread(session_record.codex_thread_id)

            await app_state.codex_thread_service.set_archived_state(
                codex_thread_id=session_record.codex_thread_id,
                archived=True,
                provider=provider,
            )
            await app_state.codex_thread_service.release_binding_if_owned(
                codex_thread_id=session_record.codex_thread_id,
                discord_thread_id=str(interaction.channel.id),
                provider=provider,
            )
            await app_state.session_service.detach_codex_thread(
                discord_thread_id=str(interaction.channel.id),
            )
            await app_state.audit_service.record(
                action="session_archived",
                guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
                discord_thread_id=str(interaction.channel.id),
                actor_id=str(interaction.user.id),
                payload={"provider": provider.value, "provider_thread_id": session_record.codex_thread_id},
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"归档 {provider_label} 会话失败：{exc}")
            return

        await interaction.response.send_message(
            f"已归档当前 {provider_label} 会话：`{session_record.codex_thread_id}`。它默认不会再出现在普通会话列表里。",
            ephemeral=True,
        )

    @group.command(name="unarchive", description="取消归档一个会话")
    @app_commands.describe(session="要取消归档的会话", scope="会话范围")
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
            route = await ensure_workspace_route(interaction)
            browser_key = f"session-browser:{route.workspace.id}"
            async with app_state.worker_pool.lease(provider, browser_key) as browser:
                thread_payload = await browser.unarchive_thread(session)
            payload_cwd = thread_payload.get("cwd")
            if isinstance(payload_cwd, str) and payload_cwd and payload_cwd != route.workspace.cwd:
                await send_interaction_error(interaction, "目标会话不属于当前工作区，无法取消归档。")
                return
            record = await app_state.codex_thread_service.sync_thread_from_payload(
                workspace_id=route.workspace.id,
                thread_payload=thread_payload,
                archived=False,
                provider=provider,
            )
            if scope == "bot" and record.source_label != "discord-bot":
                await send_interaction_error(interaction, "scope=`bot` 仅允许操作由 Discord bot 创建的会话。")
                return
            await app_state.codex_thread_service.set_archived_state(
                codex_thread_id=session,
                archived=False,
                provider=provider,
            )
            await app_state.audit_service.record(
                action="session_unarchived",
                guild_id=str(interaction.guild.id) if interaction.guild is not None else None,
                discord_thread_id=str(interaction.channel.id),
                actor_id=str(interaction.user.id),
                payload={"provider": provider.value, "provider_thread_id": session, "scope": scope},
            )
        except ValueError as exc:
            await send_interaction_error(interaction, str(exc))
            return
        except Exception as exc:
            await send_interaction_error(interaction, f"取消归档 {provider_label} 会话失败：{exc}")
            return

        await interaction.response.send_message(
            f"已取消归档 {provider_label} 会话：`{session}`。如需继续对话，请执行 `/{provider_root} session resume`。",
            ephemeral=True,
        )

    return group
