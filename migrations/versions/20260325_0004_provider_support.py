"""add provider columns for codex and claude

Revision ID: 20260325_0004
Revises: 20260324_0003
Create Date: 2026-03-25 20:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260325_0004"
down_revision = "20260324_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    provider_column = sa.Column(
        "provider",
        sa.String(length=32),
        nullable=False,
        server_default="codex",
    )

    op.add_column("discord_sessions", provider_column.copy())
    op.add_column("pending_requests", provider_column.copy())
    op.add_column("discord_turn_outputs", provider_column.copy())
    op.add_column("codex_threads", provider_column.copy())


def downgrade() -> None:
    op.drop_column("codex_threads", "provider")
    op.drop_column("discord_turn_outputs", "provider")
    op.drop_column("pending_requests", "provider")
    op.drop_column("discord_sessions", "provider")
