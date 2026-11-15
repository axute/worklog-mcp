"""Settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    db_path: str
    host: str
    port: int
    timezone: ZoneInfo
    # Sign-in through a reverse proxy (e.g. Traefik + Authelia forward auth).
    # Active only when proxy_secret is set: the proxy must send it in X-Worklog-Proxy,
    # then the user name in user_header is trusted and that user may manage tokens.
    proxy_secret: str = ""
    user_header: str = "Remote-User"
    web_users: frozenset[str] = field(default_factory=frozenset)  # empty: every user the proxy lets through
    logout_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        users = os.environ.get("WORKLOG_WEB_USERS", "")
        return cls(
            db_path=os.environ.get("WORKLOG_DB", "/data/worklog.db"),
            host=os.environ.get("WORKLOG_HOST", "0.0.0.0"),
            port=int(os.environ.get("WORKLOG_PORT", "8000")),
            timezone=ZoneInfo(os.environ.get("WORKLOG_TZ") or os.environ.get("TZ") or "Europe/Berlin"),
            proxy_secret=os.environ.get("WORKLOG_PROXY_SECRET", "").strip(),
            user_header=os.environ.get("WORKLOG_USER_HEADER", "").strip() or "Remote-User",
            web_users=frozenset(u.strip() for u in users.split(",") if u.strip()),
            logout_url=os.environ.get("WORKLOG_LOGOUT_URL", "").strip(),
        )
