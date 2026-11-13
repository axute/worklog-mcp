"""FastMCP server: tools, health route and the ASGI app."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import __version__
from .auth import WorklogTokenVerifier, current_agent
from .config import Settings
from .db import SUMMARY_MAX, Database, as_dict

INSTRUCTIONS = """\
Work log for AI agents. Record what you finished with log_entry: one entry per
result (feature, fix, configuration change, research outcome), not per command.
Never put secrets (tokens, passwords, keys) into an entry."""


def create_server(db: Database, settings: Settings) -> FastMCP:
    mcp = FastMCP(
        name="worklog",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=WorklogTokenVerifier(db),
    )
    tz = settings.timezone

    @mcp.tool(annotations={"title": "Log a work entry", "destructiveHint": False, "idempotentHint": False})
    def log_entry(
        project: Annotated[str, Field(description="Project or topic, e.g. the repository name ('website', 'backup-scripts').")],
        summary: Annotated[str, Field(description=f"One sentence: what was done. Max. {SUMMARY_MAX} characters.")],
        details: Annotated[str | None, Field(description="Optional: important commands, changed files, decisions. No secrets.")] = None,
        tags: Annotated[list[str] | None, Field(description="Optional short tags, e.g. ['update', 'docker'].")] = None,
        duration_min: Annotated[int | None, Field(description="Optional estimated duration in minutes.", ge=0)] = None,
        session_id: Annotated[str | None, Field(description="Optional id that groups the entries of one session.")] = None,
    ) -> dict[str, Any]:
        """Record a finished unit of work in the work log.

        Call this after every completed work step (feature, fix, configuration change,
        research result): one entry per result, not per single command. The agent name
        is taken from the access token. Never include secrets."""
        agent = current_agent("write")
        entry = db.add_entry(
            agent=agent, source="tool", project=project, summary=summary, details=details,
            tags=tags, duration_min=duration_min, session_id=session_id,
        )
        log.info("entry_created", extra={"entry_id": entry.id, "agent": agent, "source": "tool"})
        return {"id": entry.id, "ts": entry.ts.astimezone(tz).isoformat()}

    @mcp.tool(annotations={"title": "List work entries", "readOnlyHint": True})
    def list_entries(
        project: Annotated[str | None, Field(description="Only this project.")] = None,
        agent: Annotated[str | None, Field(description="Only entries of this agent.")] = None,
        since: Annotated[str | None, Field(description="ISO date or datetime, inclusive, e.g. '2026-11-01'.")] = None,
        until: Annotated[str | None, Field(description="ISO date or datetime, exclusive.")] = None,
        limit: Annotated[int, Field(description="Maximum number of entries (1-500).", ge=1, le=500)] = 20,
    ) -> list[dict[str, Any]]:
        """List work log entries, newest first. Times are shown in the server's time zone."""
        current_agent("read")
        entries = db.list_entries(
            project=project, agent=agent,
            since=_parse_local(since, tz), until=_parse_local(until, tz), limit=limit,
        )
        return [as_dict(e, tz) for e in entries]

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp


def _parse_local(value: str | None, tz) -> datetime | None:
    """Dates without a zone are meant in the display time zone (e.g. '2026-11-13' = local midnight)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"'{value}' is not an ISO date or datetime") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


# --------------------------------------------------------------------------- logging

class JsonFormatter(logging.Formatter):
    """One JSON object per line; never logs tokens or entry contents."""

    SAFE_EXTRA = ("entry_id", "agent", "source", "path", "status")

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in self.SAFE_EXTRA:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # stateless HTTP opens and closes a transport per request; these lines are noise
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)


log = logging.getLogger("worklog")


def create_app(settings: Settings | None = None, db: Database | None = None):
    settings = settings or Settings.from_env()
    db = db or Database(settings.db_path)
    mcp = create_server(db, settings)
    return mcp.http_app(path="/mcp", stateless_http=True)


def main() -> None:
    import uvicorn

    setup_logging()
    settings = Settings.from_env()
    app = create_app(settings)
    log.info("starting", extra={"path": "/mcp"})
    # access log off: request lines would only add noise; errors are still logged
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
