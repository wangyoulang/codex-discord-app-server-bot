"""add session model override

Revision ID: 20260515_0006
Revises: 20260430_0005
Create Date: 2026-05-15 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260515_0006"
down_revision = "20260430_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discord_sessions",
        sa.Column("model_override", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discord_sessions", "model_override")

