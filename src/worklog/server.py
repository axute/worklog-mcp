"""FastMCP server: tools, health route and the ASGI app."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from . import __version__
from .api import register_api
from .auth import WorklogTokenVerifier, current_agent
from .config import Settings
from .db import SUMMARY_MAX, Database, as_dict
from .report import group_by_day_and_project
from .webui import register_webui

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
        owner, agent = current_agent("write")
        entry = db.add_entry(
            owner=owner, agent=agent, source="tool", project=project, summary=summary, details=details,
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
        owner, _ = current_agent("read")
        entries = db.list_entries(
            owner=owner, project=project, agent=agent,
            since=_parse_local(since, tz), until=_parse_local(until, tz), limit=limit,
        )
        return [as_dict(e, tz) for e in entries]

    @mcp.tool(annotations={"title": "Search work entries", "readOnlyHint": True})
    def search_entries(
        query: Annotated[str, Field(description="Words to search for in summary, details, project and tags. All words must match; word beginnings are enough ('deploy' finds 'deployment').")],
        project: Annotated[str | None, Field(description="Only this project.")] = None,
        limit: Annotated[int, Field(description="Maximum number of results (1-500).", ge=1, le=500)] = 20,
    ) -> list[dict[str, Any]]:
        """Full text search in the work log, best matches first."""
        owner, _ = current_agent("read")
        return [as_dict(e, tz) for e in db.search_entries(query, owner=owner, project=project, limit=limit)]

    @mcp.tool(annotations={"title": "Summarize a period", "readOnlyHint": True})
    def summarize_period(
        since: Annotated[str, Field(description="Start, ISO date or datetime, inclusive, e.g. '2026-11-09'.")],
        until: Annotated[str, Field(description="End, ISO date or datetime, exclusive, e.g. '2026-11-16' for a full week.")],
        project: Annotated[str | None, Field(description="Only this project.")] = None,
    ) -> dict[str, Any]:
        """Entries of a period grouped by day and project, with durations and totals.
        Use it as the basis for weekly or monthly reports."""
        owner, _ = current_agent("read")
        start, end = _parse_local(since, tz), _parse_local(until, tz)
        if end <= start:
            raise ValueError("until must be after since")
        result = group_by_day_and_project(db.entries_between(start, end, owner=owner, project=project), tz)
        result["since"], result["until"] = start.isoformat(), end.isoformat()
        return result

    @mcp.tool(annotations={"title": "List projects", "readOnlyHint": True})
    def list_projects() -> list[dict[str, Any]]:
        """Known projects with number of entries, total logged minutes and last entry, most recent first.
        Use the exact project names from here when logging, so entries end up in the same project."""
        owner, _ = current_agent("read")
        return [{**p, "last_ts": p["last_ts"].astimezone(tz).isoformat()} for p in db.list_projects(owner=owner)]

    register_api(mcp, db, settings)
    register_webui(mcp, db, settings)
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

    SAFE_EXTRA = ("entry_id", "agent", "source", "path", "status", "by", "scopes")

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


def setup_logging(stream=None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
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
    db = db or Database(settings.db_path, legacy_owner=settings.legacy_owner)
    mcp = create_server(db, settings)
    return mcp.http_app(path="/mcp", stateless_http=True)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="worklog-server", description="worklog MCP server")
    parser.add_argument("--stdio", action="store_true",
                        help="talk MCP over stdin/stdout (the client starts the server, e.g. with docker run -i); "
                             "user and agent come from WORKLOG_OWNER and WORKLOG_AGENT")
    args = parser.parse_args(argv)
    if args.stdio:
        run_stdio()
        return

    import uvicorn

    setup_logging()
    settings = Settings.from_env()
    app = create_app(settings)
    log.info("starting", extra={"path": "/mcp"})
    # access log off: request lines would only add noise; errors are still logged
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, access_log=False)


def run_stdio() -> None:
    """stdio transport: stdout belongs to the protocol, so logs go to stderr."""
    from .auth import set_local_identity

    setup_logging(sys.stderr)
    settings = Settings.from_env()
    if not settings.stdio_owner:
        sys.exit("worklog-server --stdio: set WORKLOG_OWNER (your user name) and optionally WORKLOG_AGENT")
    try:
        set_local_identity(settings.stdio_owner, settings.stdio_agent)
    except ValueError as e:
        sys.exit(f"worklog-server --stdio: {e}")
    db = Database(settings.db_path, legacy_owner=settings.legacy_owner)
    mcp = create_server(db, settings)
    log.info("starting", extra={"path": "stdio", "agent": settings.stdio_agent})
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
