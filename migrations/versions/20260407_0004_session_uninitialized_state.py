"""add uninitialized session state

Revision ID: 20260407_0004
Revises: 20260324_0003
Create Date: 2026-04-07 12:00:00
"""

from __future__ import annotations

from alembic import op


revision = "20260407_0004"
down_revision = "20260324_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE discord_sessions
        SET status = 'uninitialized',
            active_turn_id = NULL,
            last_bot_message_id = NULL
        WHERE codex_thread_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE discord_sessions
        SET status = 'ready'
        WHERE status = 'uninitialized'
        """
    )
