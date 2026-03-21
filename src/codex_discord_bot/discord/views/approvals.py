from __future__ import annotations

import discord


DECISION_LABELS = {
    "accept": ("批准", discord.ButtonStyle.success),
    "acceptForSession": ("本会话批准", discord.ButtonStyle.primary),
    "decline": ("拒绝", discord.ButtonStyle.danger),
    "cancel": ("取消", discord.ButtonStyle.secondary),
}


class ApprovalDecisionButton(discord.ui.Button["ApprovalDecisionView"]):
    def __init__(self, *, local_request_id: str, decision: str) -> None:
        label, style = DECISION_LABELS[decision]
        super().__init__(label=label, style=style, custom_id=f"approval:{local_request_id}:{decision}")
        self.local_request_id = local_request_id
        self.decision = decision

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        await self.view.handle_decision(interaction, self.decision)


class ApprovalDecisionView(discord.ui.View):
    def __init__(
        self,
        app_state: object,
        *,
        local_request_id: str,
        decisions: tuple[str, ...],
    ) -> None:
        super().__init__(timeout=900)
        self.app_state = app_state
        self.local_request_id = local_request_id
        for decision in decisions:
            if decision in DECISION_LABELS:
                self.add_item(ApprovalDecisionButton(local_request_id=local_request_id, decision=decision))

    async def handle_decision(self, interaction: discord.Interaction, decision: str) -> None:
        can_manage = bool(
            isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        )
        actor_id = str(interaction.user.id)
        allowed = await self.app_state.approval_service.can_resolve(
            self.local_request_id,
            actor_id=actor_id,
            can_manage=can_manage,
        )
        if not allowed:
            await interaction.response.send_message("你无权处理该审批请求。", ephemeral=True)
            return

        resolved = await self.app_state.approval_service.resolve_request(
            self.local_request_id,
            decision=decision,
            actor_id=actor_id,
        )
        if not resolved:
            await interaction.response.send_message("该审批请求已失效。", ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n\n已处理：`{decision}` by <@{actor_id}>",
            view=self,
        )
