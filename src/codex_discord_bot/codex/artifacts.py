from __future__ import annotations

from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def thread_dir(self, codex_thread_id: str) -> Path:
        path = self.root / codex_thread_id
        path.mkdir(parents=True, exist_ok=True)
        return path
