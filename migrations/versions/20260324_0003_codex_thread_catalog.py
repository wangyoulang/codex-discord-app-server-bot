"""add codex thread catalog

Revision ID: 20260324_0003
Revises: 20260323_0002
Create Date: 2026-03-24 18:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260324_0003"
down_revision = "20260323_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "codex_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codex_thread_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column("source_label", sa.String(length=120), nullable=True),
        sa.Column("preview", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("thread_status", sa.String(length=32), nullable=False),
        sa.Column("bound_discord_thread_id", sa.String(length=32), nullable=True),
        sa.Column("thread_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thread_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("codex_thread_id", name="uq_codex_thread_id"),
    )


def downgrade() -> None:
    op.drop_table("codex_threads")
