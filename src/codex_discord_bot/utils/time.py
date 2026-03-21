from __future__ import annotations

from datetime import UTC
from datetime import datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
