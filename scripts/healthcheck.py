from __future__ import annotations

import json
import shutil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_discord_bot.config import load_settings


def main() -> None:
    settings = load_settings()
    result = {
        "discord_token_configured": bool(settings.discord_bot_token),
        "codex_bin": settings.codex_bin or "codex",
        "codex_bin_found": shutil.which(settings.codex_bin or "codex") is not None,
        "database_url": settings.database_url,
        "state_dir": str(settings.state_dir),
        "artifact_dir": str(settings.artifact_dir),
        "log_dir": str(settings.log_dir),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
