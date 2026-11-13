"""Settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    db_path: str
    host: str
    port: int
    timezone: ZoneInfo

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get("WORKLOG_DB", "/data/worklog.db"),
            host=os.environ.get("WORKLOG_HOST", "0.0.0.0"),
            port=int(os.environ.get("WORKLOG_PORT", "8000")),
            timezone=ZoneInfo(os.environ.get("WORKLOG_TZ") or os.environ.get("TZ") or "Europe/Berlin"),
        )
