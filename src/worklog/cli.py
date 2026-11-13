"""Command line: token management (more commands follow in later phases).

    worklog token create <name> [--scopes write,read]
    worklog token list
    worklog token revoke <name>
"""

from __future__ import annotations

import argparse
import sys

from .auth import create_token
from .config import Settings
from .db import Database


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
