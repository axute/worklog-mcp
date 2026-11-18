"""Command line for the container.

    worklog token create <name> --owner USER [--scopes write,read]
    worklog token list [--owner USER]
    worklog token revoke <name> --owner USER
    worklog export --owner USER [--format md|csv] [--since DATE] [--until DATE] [--project P] [--output FILE]
    worklog report --owner USER [--period last-week|this-week|last-month|this-month] [--project P] [--dir DIR]
    worklog backup [--dir DIR] [--keep 14]

Every entry and token belongs to a user (the owner). With sign-in through a
reverse proxy (Authelia) the owner is the user name the proxy reports.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .auth import create_token
from .config import Settings
from .db import Database, utcnow
from .report import PERIODS, period_bounds, summary_line, to_csv, to_markdown


def _local(value: str, tz) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _fmt(dt, tz) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worklog", description="worklog MCP server administration")
    parser.add_argument("--db", help="database file (default: $WORKLOG_DB or /data/worklog.db)")
    parser.add_argument("--legacy-owner", help="user for data from before multi-user support; needed once "
                                               "for the migration (default: $WORKLOG_LEGACY_OWNER)")
    sub = parser.add_subparsers(dest="command", required=True)

    tok = sub.add_parser("token", help="manage access tokens").add_subparsers(dest="action", required=True)
    c = tok.add_parser("create", help="create a token; the plain text is shown once")
    c.add_argument("name", help="agent name, e.g. claude-code-laptop")
    c.add_argument("--owner", required=True, help="user the token belongs to, e.g. the Authelia user name")
    c.add_argument("--scopes", default="write,read", help="comma separated: write, read (default: write,read)")
    ls = tok.add_parser("list", help="list tokens (of all users, or one)")
    ls.add_argument("--owner")
    r = tok.add_parser("revoke", help="revoke a token immediately")
    r.add_argument("name")
    r.add_argument("--owner", required=True)

    ex = sub.add_parser("export", help="export entries as Markdown or CSV")
    ex.add_argument("--owner", required=True, help="whose entries")
    ex.add_argument("--format", choices=["md", "csv"], default="md")
    ex.add_argument("--since", help="ISO date, inclusive (default: everything)")
    ex.add_argument("--until", help="ISO date, exclusive (default: now)")
    ex.add_argument("--project")
    ex.add_argument("--output", "-o", help="file instead of stdout")

    rp = sub.add_parser("report", help="Markdown report of a week or month, e.g. for a weekly notification")
    rp.add_argument("--owner", required=True, help="whose entries")
    rp.add_argument("--period", choices=PERIODS, default="last-week", help="calendar week or month (default: last-week)")
    rp.add_argument("--project")
    rp.add_argument("--dir", help="write worklog-<owner>-<period>.md there and print a one-line summary "
                                  "(for notifications); default: Markdown to stdout")

    bk = sub.add_parser("backup", help="consistent copy of the database; keeps the newest N")
    bk.add_argument("--dir", help="target directory (default: backups/ next to the database)")
    bk.add_argument("--keep", type=int, default=14, help="number of backups to keep (default 14)")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    try:
        db = Database(args.db or settings.db_path, legacy_owner=args.legacy_owner or settings.legacy_owner)
    except (RuntimeError, ValueError) as e:  # e.g. migration 4 needs the legacy owner
        print(f"error: {e}", file=sys.stderr)
        return 1
    tz = settings.timezone

    try:
        if args.command == "token" and args.action == "create":
            scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
            token = create_token(db, args.owner, args.name, scopes)
            print(f"Token '{args.name}' for user '{args.owner}' (scopes: {','.join(sorted(scopes))}).")
            print("It is shown only now; store it in a password manager:\n")
            print(token)
        elif args.command == "token" and args.action == "list":
            rows = db.list_tokens(owner=args.owner)
            if not rows:
                print("no tokens")
            for t in rows:
                state = f"revoked {_fmt(t.revoked_at, tz)}" if t.revoked_at else "active"
                print(f"{t.owner:<16} {t.name:<28} {','.join(t.scopes):<11} created {_fmt(t.created_at, tz)}  "
                      f"last used {_fmt(t.last_used_at, tz):<16}  {state}")
        elif args.command == "token" and args.action == "revoke":
            db.revoke_token(args.owner, args.name)
            print(f"Token '{args.name}' of '{args.owner}' revoked.")
        elif args.command == "export":
            since = _local(args.since, tz) if args.since else datetime(1970, 1, 1, tzinfo=tz)
            until = _local(args.until, tz) if args.until else utcnow() + timedelta(seconds=1)
            entries = db.entries_between(since, until, owner=args.owner, project=args.project)
            text = to_csv(entries, tz) if args.format == "csv" else to_markdown(entries, tz)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
                print(f"{len(entries)} entries written to {args.output}", file=sys.stderr)
            else:
                sys.stdout.write(text)
        elif args.command == "report":
            start, end, label = period_bounds(args.period, tz)
            entries = db.entries_between(start, end, owner=args.owner, project=args.project)
            title = f"Work log {label}" + (f" · {args.project}" if args.project else "")
            text = to_markdown(entries, tz, title=title)
            line = summary_line(entries, label + (f" {args.project}" if args.project else ""))
            if args.dir:
                name = "-".join(filter(None, ["worklog", args.owner, label, args.project])) + ".md"
                name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
                target = Path(args.dir) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                print(line)
                print(f"report written: {target}", file=sys.stderr)
            else:
                sys.stdout.write(text)
                print(line, file=sys.stderr)
        elif args.command == "backup":
            if args.keep < 1:
                raise ValueError("--keep must be at least 1")
            target_dir = Path(args.dir) if args.dir else Path(db.path).parent / "backups"
            target = target_dir / f"worklog-{datetime.now(tz).strftime('%Y%m%d-%H%M%S')}.db"
            db.backup(target)
            old = sorted(target_dir.glob("worklog-*.db"))[:-args.keep]
            for f in old:
                f.unlink()
            print(f"backup written: {target} (removed {len(old)} old)")
    except KeyError as e:
        print(f"error: unknown token {e}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
