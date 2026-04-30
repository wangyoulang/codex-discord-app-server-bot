"""add token usage snapshot to turn outputs

Revision ID: 20260430_0005
Revises: 20260407_0004
Create Date: 2026-04-30 10:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0005"
down_revision = "20260407_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discord_turn_outputs",
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discord_turn_outputs", "token_usage_json")
