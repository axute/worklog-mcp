"""Command line for the container.

    worklog token create <name> [--scopes write,read]
    worklog token list
    worklog token revoke <name>
    worklog export [--format md|csv] [--since DATE] [--until DATE] [--project P] [--output FILE]
    worklog backup [--dir DIR] [--keep 14]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .auth import create_token
from .config import Settings
from .db import Database, utcnow
from .report import to_csv, to_markdown


def _local(value: str, tz) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _fmt(dt, tz) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worklog", description="worklog MCP server administration")
    parser.add_argument("--db", help="database file (default: $WORKLOG_DB or /data/worklog.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    tok = sub.add_parser("token", help="manage access tokens").add_subparsers(dest="action", required=True)
    c = tok.add_parser("create", help="create a token; the plain text is shown once")
    c.add_argument("name", help="agent name, e.g. claude-code-laptop")
    c.add_argument("--scopes", default="write,read", help="comma separated: write, read (default: write,read)")
    tok.add_parser("list", help="list tokens")
    r = tok.add_parser("revoke", help="revoke a token immediately")
    r.add_argument("name")

    ex = sub.add_parser("export", help="export entries as Markdown or CSV")
    ex.add_argument("--format", choices=["md", "csv"], default="md")
    ex.add_argument("--since", help="ISO date, inclusive (default: everything)")
    ex.add_argument("--until", help="ISO date, exclusive (default: now)")
    ex.add_argument("--project")
    ex.add_argument("--output", "-o", help="file instead of stdout")

    bk = sub.add_parser("backup", help="consistent copy of the database; keeps the newest N")
    bk.add_argument("--dir", help="target directory (default: backups/ next to the database)")
    bk.add_argument("--keep", type=int, default=14, help="number of backups to keep (default 14)")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    db = Database(args.db or settings.db_path)
    tz = settings.timezone

    try:
        if args.command == "token" and args.action == "create":
            scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
            token = create_token(db, args.name, scopes)
            print(f"Token for '{args.name}' (scopes: {','.join(sorted(scopes))}).")
            print("It is shown only now; store it in a password manager:\n")
            print(token)
        elif args.command == "token" and args.action == "list":
            rows = db.list_tokens()
            if not rows:
                print("no tokens")
            for t in rows:
                state = f"revoked {_fmt(t.revoked_at, tz)}" if t.revoked_at else "active"
                print(f"{t.name:<28} {','.join(t.scopes):<11} created {_fmt(t.created_at, tz)}  "
                      f"last used {_fmt(t.last_used_at, tz):<16}  {state}")
        elif args.command == "token" and args.action == "revoke":
            db.revoke_token(args.name)
            print(f"Token '{args.name}' revoked.")
        elif args.command == "export":
            since = _local(args.since, tz) if args.since else datetime(1970, 1, 1, tzinfo=tz)
            until = _local(args.until, tz) if args.until else utcnow() + timedelta(seconds=1)
            entries = db.entries_between(since, until, project=args.project)
            text = to_csv(entries, tz) if args.format == "csv" else to_markdown(entries, tz)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
                print(f"{len(entries)} entries written to {args.output}", file=sys.stderr)
            else:
                sys.stdout.write(text)
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
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
