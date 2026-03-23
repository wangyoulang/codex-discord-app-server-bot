"""add discord turn output streaming state

Revision ID: 20260323_0002
Revises: 20260321_0001
Create Date: 2026-03-23 15:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260323_0002"
down_revision = "20260321_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discord_turn_outputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("discord_thread_id", sa.String(length=32), nullable=False),
        sa.Column("codex_thread_id", sa.String(length=64), nullable=True),
        sa.Column("codex_turn_id", sa.String(length=64), nullable=False),
        sa.Column("control_message_id", sa.String(length=32), nullable=True),
        sa.Column("preview_message_ids_json", sa.JSON(), nullable=True),
        sa.Column("final_message_ids_json", sa.JSON(), nullable=True),
        sa.Column("active_agent_item_id", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("codex_turn_id", name="uq_discord_turn_output_turn"),
    )


def downgrade() -> None:
    op.drop_table("discord_turn_outputs")
