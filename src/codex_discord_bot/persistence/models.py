from __future__ import annotations

from typing import Any

from sqlalchemy import DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from codex_discord_bot.persistence.enums import PendingRequestType
from codex_discord_bot.persistence.enums import SessionStatus
from codex_discord_bot.utils.time import utc_now


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String(32), nullable=False)
    forum_channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    cwd: Mapped[str] = mapped_column(Text, nullable=False)
    default_model: Mapped[str] = mapped_column(String(120), nullable=False)
    default_reasoning_effort: Mapped[str] = mapped_column(String(32), nullable=False)
    sandbox_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    sessions: Mapped[list["DiscordSession"]] = relationship(back_populates="workspace")


class DiscordSession(Base):
    __tablename__ = "discord_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    discord_thread_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    codex_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(
        SqlEnum(SessionStatus, native_enum=False),
        default=SessionStatus.ready,
        nullable=False,
    )
    last_bot_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship(back_populates="sessions")


class PendingRequest(Base):
    __tablename__ = "pending_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    discord_thread_id: Mapped[str] = mapped_column(String(32), nullable=False)
    codex_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_type: Mapped[PendingRequestType] = mapped_column(
        SqlEnum(PendingRequestType, native_enum=False),
        nullable=False,
    )
    available_decisions_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guild_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discord_thread_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
