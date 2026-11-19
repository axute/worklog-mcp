"""FastMCP server: tools, health route and the ASGI app."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from . import __version__
from .api import register_api
from .auth import WorklogTokenVerifier, current_agent
from .config import Settings
from .db import SUMMARY_MAX, Database, as_dict
from .dupes import WINDOW_HOURS, likely_same
from .report import group_by_day_and_project, period_bounds
from .times import parse_local as _parse_local, work_time
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

    def write_entry(owner: str, agent: str, item: dict[str, Any], when: datetime | None) -> dict[str, Any]:
        entry = db.add_entry(owner=owner, agent=agent, source="tool", ts=when, **item)
        log.info("entry_created", extra={"entry_id": entry.id, "agent": agent, "source": "tool"})
        result: dict[str, Any] = {"id": entry.id, "ts": entry.ts.astimezone(tz).isoformat()}
        # same work logged twice? tell the agent, so it corrects instead of adding more
        window = timedelta(hours=WINDOW_HOURS)
        near = db.list_entries(owner=owner, project=entry.project, since=entry.ts - window,
                               until=entry.ts + window, limit=100)
        twins = [e.id for e in near if e.id != entry.id and likely_same(e, entry)]
        if twins:
            result["possible_duplicate_of"] = twins
            result["hint"] = ("A very similar entry was logged recently (possible_duplicate_of). If it is the "
                              "same work, don't log it again: correct the existing entry with update_entry. "
                              "The user can merge duplicates in the web view.")
        return result

    @mcp.tool(annotations={"title": "Log a work entry", "destructiveHint": False, "idempotentHint": False})
    def log_entry(
        project: Annotated[str, Field(description="Project or topic, e.g. the repository name ('website', 'backup-scripts').")],
        summary: Annotated[str, Field(description=f"One sentence: what was done. Max. {SUMMARY_MAX} characters.")],
        details: Annotated[str | None, Field(description="Optional: important commands, changed files, decisions. No secrets.")] = None,
        tags: Annotated[list[str] | None, Field(description="Optional short tags, e.g. ['update', 'docker'].")] = None,
        duration_min: Annotated[int | None, Field(description="Optional estimated duration in minutes.", ge=0)] = None,
        session_id: Annotated[str | None, Field(description="Optional id that groups the entries of one session.")] = None,
        ts: Annotated[str | None, Field(description=TS_HELP)] = None,
    ) -> dict[str, Any]:
        """Record a finished unit of work in the work log.

        Call this after every completed work step (feature, fix, configuration change,
        research result): one entry per result, not per single command. The agent name
        is taken from the access token. Never include secrets. For work done earlier,
        set ts to when it happened (several at once: log_entries)."""
        owner, agent = current_agent("write")
        when = work_time(ts, tz)
        return write_entry(owner, agent, {"project": project, "summary": summary, "details": details, "tags": tags,
                                          "duration_min": duration_min, "session_id": session_id}, when)

    @mcp.tool(annotations={"title": "Log several work entries", "destructiveHint": False, "idempotentHint": False})
    def log_entries(
        entries: Annotated[list[NewEntry], Field(description=f"Up to {LOG_MANY_MAX} entries.", min_length=1,
                                                   max_length=LOG_MANY_MAX)],
    ) -> dict[str, Any]:
        """Record several units of work at once, typically the history of a project logged afterwards
        (each with its own ts). Check first with list_entries / search_entries what is already logged,
        so nothing is entered twice. All entries are checked before any is written."""
        owner, agent = current_agent("write")
        prepared = []
        for i, e in enumerate(entries):
            try:
                if not e.project.strip() or not e.summary.strip() or len(e.summary.strip()) > SUMMARY_MAX:
                    raise ValueError(f"project and summary (1-{SUMMARY_MAX} characters) are required")
                prepared.append((e.model_dump(exclude={"ts"}), work_time(e.ts, tz)))
            except ValueError as err:
                raise ValueError(f"entry {i}: {err}") from None
        results = [write_entry(owner, agent, item, when) for item, when in prepared]
        dupes = [r for r in results if "possible_duplicate_of" in r]
        out: dict[str, Any] = {"created": len(results), "entries": [{k: v for k, v in r.items() if k != "hint"}
                                                                    for r in results]}
        if dupes:
            out["hint"] = (f"{len(dupes)} of them look like entries that existed already (possible_duplicate_of). "
                           "Check them; the user can merge duplicates in the web view.")
        return out

    @mcp.tool(annotations={"title": "Correct a work entry", "destructiveHint": False, "idempotentHint": True})
    def update_entry(
        entry_id: Annotated[int, Field(description="Id of the entry, as returned by log_entry or list_entries.")],
        project: Annotated[str | None, Field(description="New project name.")] = None,
        summary: Annotated[str | None, Field(description=f"New summary, max. {SUMMARY_MAX} characters.")] = None,
        details: Annotated[str | None, Field(description="New details (replaces the old text).")] = None,
        tags: Annotated[list[str] | None, Field(description="New tags (replace the old ones).")] = None,
        duration_min: Annotated[int | None, Field(description="New duration in minutes.", ge=0)] = None,
    ) -> dict[str, Any]:
        """Correct an entry this agent logged earlier, e.g. a wrong project name or a typo.
        Only the given fields change. Entries of other agents cannot be changed; to remove
        an entry, the user deletes it in the web view."""
        owner, agent = current_agent("write")
        changes = {k: v for k, v in (("project", project), ("summary", summary), ("details", details),
                                     ("tags", tags), ("duration_min", duration_min)) if v is not None}
        try:
            entry = db.update_entry(owner=owner, entry_id=entry_id, agent=agent, **changes)
        except KeyError:
            raise ValueError(f"no entry {entry_id} written by '{agent}'") from None
        log.info("entry_updated", extra={"entry_id": entry.id, "agent": agent, "source": "tool"})
        return as_dict(entry, tz)

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
        """Known projects with number of entries, total logged minutes, last entry and the user's notes
        (description, customer, archived), most recent first. Use the exact project names from here when
        logging, so entries end up in the same project. Archived projects are finished: log to them only
        if the work clearly belongs there."""
        owner, _ = current_agent("read")
        return [{**p, "last_ts": p["last_ts"].astimezone(tz).isoformat()} for p in db.list_projects(owner=owner)]

    # ------------------------------------------------------------------ prompts

    def report_prompt(kind: str, ago: int, project: str | None, language: str | None) -> str:
        start, end, label = period_bounds(kind, tz, ago=ago)
        scope = f" for the project '{project}'" if project else ""
        only = f", project='{project}'" if project else ""
        return (
            f"Write a {'weekly' if kind == 'week' else 'monthly'} work report for {label}{scope} "
            f"({start:%Y-%m-%d} to {(end - timedelta(days=1)):%Y-%m-%d}).\n\n"
            f"1. Call the worklog tool summarize_period with since='{start:%Y-%m-%d}', "
            f"until='{end:%Y-%m-%d}'{only}.\n"
            "2. Write the report from that data only; do not add anything that is not in the log:\n"
            "   - a short overview: total time, number of entries, the main topics;\n"
            "   - per project (largest first): logged time and a few bullet points with the results, "
            "similar entries merged into one point;\n"
            "   - decisions and open points, if the entries mention any.\n"
            "Keep it short and concrete. If there are no entries, say so in one sentence.\n"
            f"Language: {language or 'the language the user writes in'}."
        )

    @mcp.prompt(name="weekly_report", description="Report of a calendar week (Monday to Sunday) from the work log.")
    def weekly_report(
        weeks_ago: Annotated[int, Field(description="0 = this week, 1 = last week (default), 2 = the week before, ...", ge=0)] = 1,
        project: Annotated[str | None, Field(description="Only this project.")] = None,
        language: Annotated[str | None, Field(description="Language of the report, e.g. 'German'.")] = None,
    ) -> str:
        return report_prompt("week", weeks_ago, project, language)

    @mcp.prompt(name="monthly_report", description="Report of a calendar month from the work log.")
    def monthly_report(
        months_ago: Annotated[int, Field(description="0 = this month, 1 = last month (default), ...", ge=0)] = 1,
        project: Annotated[str | None, Field(description="Only this project.")] = None,
        language: Annotated[str | None, Field(description="Language of the report, e.g. 'German'.")] = None,
    ) -> str:
        return report_prompt("month", months_ago, project, language)

    @mcp.prompt(name="standup", description="Short stand-up: what I did on the last working day and today so far.")
    def standup(
        language: Annotated[str | None, Field(description="Language, e.g. 'German'.")] = None,
    ) -> str:
        now = datetime.now(tz)
        today = now.date()
        last = today - timedelta(days=3 if today.weekday() == 0 else 2 if today.weekday() == 6 else 1)
        return (
            f"Prepare my stand-up. Today is {today:%A, %Y-%m-%d}.\n\n"
            f"1. Call worklog summarize_period with since='{last}' and until='{today}' (the last working day), "
            f"and again with since='{today}' and until='{today + timedelta(days=1)}' (today so far).\n"
            "2. Answer in three short parts, bullet points, results instead of activities:\n"
            "   - Done (last working day), grouped by project;\n"
            "   - Today so far;\n"
            "   - Open / next: only what the entries mention as open, follow-up or blocked – don't make up plans.\n"
            "Keep it short enough to read out in a minute. If there are no entries, say so.\n"
            f"Language: {language or 'the language I write in'}."
        )

    @mcp.prompt(name="backfill_project",
                description="Log the history of an earlier project afterwards, with the real dates.")
    def backfill_project(
        project: Annotated[str, Field(description="Project name as it should appear in the log (or as it is already logged).")],
        source: Annotated[str | None, Field(description="Where the history is, e.g. a repository path, a changelog or 'this conversation'. Default: the current working directory.")] = None,
        since: Annotated[str | None, Field(description="Only work from this date on, e.g. '2025-01-01'.")] = None,
        language: Annotated[str | None, Field(description="Language of the entries, e.g. 'German'.")] = None,
    ) -> str:
        where = source or "the current working directory (repository)"
        period = f" from {since} on" if since else ""
        return (
            f"Log the history of the project '{project}'{period} into the work log, afterwards and with the real dates.\n\n"
            f"1. Check what is already logged: worklog list_entries with project='{project}' (limit 500) and, if the "
            "project may have run under another name, list_projects. Do not log anything twice.\n"
            f"2. Reconstruct the history from {where}: for a git repository `git log --date=iso --stat` (or "
            "--reverse), tags and merge commits; also CHANGELOG, README, docs, issue notes. Read, don't change anything.\n"
            "3. Group it into results: one entry per finished feature, fix, configuration change or decision – "
            "not one per commit. Small commits on the same topic and day belong together.\n"
            "4. For each entry: summary = one sentence about the result; details = the important commits/files "
            "and reasons; ts = when it was finished (ISO datetime from the commit date); tags including "
            "'backfill'; duration_min only if it can be derived (e.g. first to last commit of a short session), "
            "otherwise leave it out. Never invent work, dates or durations that the sources don't show. "
            "No secrets.\n"
            f"5. Write them with worklog log_entries (up to {LOG_MANY_MAX} per call), oldest first, project='{project}'. "
            "If an answer reports possible_duplicate_of, compare and tell the user.\n"
            "6. Finish with a short overview: number of entries, period covered, and what you left out and why.\n"
            f"Language of the entries: {language or 'the language the user writes in'}."
        )

    @mcp.prompt(name="backfill_my_commits",
                description="Log the history of the open project afterwards, from your own git commits only.")
    def backfill_my_commits(
        since: Annotated[str | None, Field(description="Only work from this date on, e.g. '2025-01-01'.")] = None,
        language: Annotated[str | None, Field(description="Language of the entries, e.g. 'German'.")] = None,
    ) -> str:
        period = f" from {since} on" if since else ""
        return (
            f"Log the history of THIS project (the project or working directory that is open) into my work log, "
            f"afterwards and with the real dates{period}. Only count work I did myself, i.e. only my commits.\n\n"
            "1. Project name: take the name of the project or git repository (folder or remote name), lower case, "
            "spaces as '-'. Check worklog list_projects for it, also under a similar name (without prefix or "
            "suffix); if it exists, use exactly that name. If it is not clear, ask me.\n"
            "2. Who am I: read `git config user.name` and `git config user.email`, show me "
            "`git shortlog -sne --all` and mark which authors you think are me (also old e-mail addresses or "
            "spellings). Wait until I confirm; from then on only commits of these identities count "
            "(`git log --author=...` for each).\n"
            "3. Check what is already logged: worklog list_entries with that project name and limit 500. "
            "Do not log anything twice.\n"
            "4. Reconstruct, read only, change nothing: `git log --reverse --date=iso --stat --author=...`, tags "
            "and merges I was part of; CHANGELOG, README and docs only as context for my commits, never as entries "
            "for work of others.\n"
            "5. Group into results: one entry per finished feature, fix, configuration change or decision – not "
            "one per commit. Small commits on the same topic and day belong together; formatting, merge and "
            "version commits only go into the details.\n"
            "6. Each entry: summary = one sentence about the result; details = the important commits (short hash), "
            "files and reasons; ts = when it was finished (ISO datetime from the commit date); tags including "
            "'backfill'; duration_min only if the commits show it (first to last commit of a short session on one "
            "day), otherwise leave it out. Never invent work, dates or durations. No secrets (tokens, passwords, "
            "keys, customer data), also not from commit messages, branch names or config files.\n"
            "7. Preview: show me the planned entries first as a table (date, summary, tags, duration, number of "
            "commits) and wait for my OK; apply my changes before writing.\n"
            f"8. After my OK: write them with worklog log_entries (up to {LOG_MANY_MAX} per call), oldest first. "
            "If an answer reports possible_duplicate_of, compare and tell me instead of deleting anything.\n"
            "9. Finish with: number of entries, period covered, how many of my commits went in, and what you left "
            "out and why.\n"
            f"Language of the entries: {language or 'the language I write in'}."
        )

    register_api(mcp, db, settings)
    register_webui(mcp, db, settings)
    return mcp


LOG_MANY_MAX = 100
TS_HELP = ("Optional: when the work was done, for entries logged afterwards. ISO date ('2025-03-14', counted as "
           "noon) or datetime ('2025-03-14T16:30'); without an offset in the server's time zone. Default: now. "
           "Not in the future.")


class NewEntry(BaseModel):
    project: str = Field(description="Project or topic, e.g. the repository name.")
    summary: str = Field(description=f"One sentence: what was done. Max. {SUMMARY_MAX} characters.")
    details: str | None = Field(None, description="Optional: important commands, changed files, decisions. No secrets.")
    tags: list[str] | None = Field(None, description="Optional short tags.")
    duration_min: int | None = Field(None, ge=0, description="Optional duration in minutes; leave out if unknown.")
    session_id: str | None = Field(None, description="Optional id that groups entries.")
    ts: str | None = Field(None, description=TS_HELP)


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
    db = Database(settings.db_path, legacy_owner=settings.legacy_owner)
    app = create_app(settings, db)
    if settings.scheduler:
        from .notify import start_scheduler
        start_scheduler(db, settings)  # reports set up in the web view
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
