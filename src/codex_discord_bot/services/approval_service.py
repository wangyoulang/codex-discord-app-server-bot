from __future__ import annotations

from codex_discord_bot.persistence.db import Database


class ApprovalService:
    def __init__(self, db: Database) -> None:
        self.db = db
