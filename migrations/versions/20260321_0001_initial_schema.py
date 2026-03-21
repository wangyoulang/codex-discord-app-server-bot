"""initial schema

Revision ID: 20260321_0001
Revises:
Create Date: 2026-03-21 15:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260321_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.String(length=32), nullable=False),
        sa.Column("forum_channel_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("cwd", sa.Text(), nullable=False),
        sa.Column("default_model", sa.String(length=120), nullable=False),
        sa.Column("default_reasoning_effort", sa.String(length=32), nullable=False),
        sa.Column("sandbox_mode", sa.String(length=32), nullable=False),
        sa.Column("approval_policy", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("guild_id", "forum_channel_id", name="uq_workspace_forum_channel"),
    )
    op.create_table(
        "discord_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("discord_thread_id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("codex_thread_id", sa.String(length=64), nullable=True),
        sa.Column("active_turn_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_bot_message_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("discord_thread_id", name="uq_discord_session_thread"),
    )
    op.create_table(
        "pending_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("discord_thread_id", sa.String(length=32), nullable=False),
        sa.Column("codex_thread_id", sa.String(length=64), nullable=True),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("item_id", sa.String(length=64), nullable=True),
        sa.Column("request_type", sa.String(length=32), nullable=False),
        sa.Column("available_decisions_json", sa.JSON(), nullable=True),
        sa.Column("message_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_pending_request_id"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guild_id", sa.String(length=32), nullable=True),
        sa.Column("discord_thread_id", sa.String(length=32), nullable=True),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("pending_requests")
    op.drop_table("discord_sessions")
    op.drop_table("workspaces")
