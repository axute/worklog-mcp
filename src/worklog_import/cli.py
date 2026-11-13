"""worklog-import command line.

    worklog-import prepare --source claude-code [--path DIR] [--since DATE] [--rules FILE]
                           [--summarizer claude|none] [--model M] [--limit N] [--exclude ID ...]
    worklog-import upload  [--file import-preview.jsonl] [--url URL] [--update] [--dry-run]

prepare writes import-preview.jsonl (what will be uploaded) and import-preview.md
(readable overview). Edit or delete lines in the .jsonl, or set "skip": true,
before uploading. Summaries are cached in import-cache.json, so a second run only
summarizes new sessions.

upload reads the token from $WORKLOG_TOKEN (or --token-file) and the server URL
from $WORKLOG_URL (or --url).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .rules import load_rules
from .sources import (Session, claude_ai_sessions, claude_code_sessions, copilot_sessions,
                      default_claude_dir, filter_sessions, parse_ts)
from .summarize import PROMPT_VERSION, summarize_claude, summarize_simple, to_entries
from .upload import upload


def _date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = parse_ts(value if "T" in value else value + "T00:00:00")
    if dt is None:
        raise SystemExit(f"error: '{value}' is not a date")
    return dt


def _load_cache(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def cmd_prepare(args: argparse.Namespace) -> int:
    if args.source == "claude-code":
        root = Path(args.path).expanduser() if args.path else default_claude_dir()
        sessions_iter = claude_code_sessions(root)
    elif args.source == "claude-ai":
        if not args.path:
            raise SystemExit("error: --path to the claude.ai export (ZIP, folder or conversations.json) is required")
        sessions_iter = claude_ai_sessions(Path(args.path).expanduser())
    else:
        if not args.path:
            raise SystemExit("error: --path to the Copilot activity CSV is required")
        sessions_iter = copilot_sessions(Path(args.path).expanduser(), tz=args.tz, gap_hours=args.gap_hours)

    sessions: List[Session] = list(filter_sessions(
        sessions_iter, _date(args.since), _date(args.until), args.min_age_hours, args.exclude or []))
    sessions.sort(key=lambda s: s.start)
    if args.limit:
        sessions = sessions[: args.limit]

    rules = load_rules(Path(args.rules) if args.rules else None)
    cache_path = Path(args.cache)
    cache = _load_cache(cache_path)
    out_jsonl, out_md = Path(args.out + ".jsonl"), Path(args.out + ".md")

    previews: List[Dict[str, Any]] = []
    cost = 0.0
    print(f"{len(sessions)} sessions ({args.source}), summarizer: {args.summarizer}", file=sys.stderr)
    for n, s in enumerate(sessions, 1):
        project = rules.match(s) or rules.default
        key = (f"{s.external_base}@{s.fingerprint()}:{args.summarizer}" + (f":{args.model}" if args.model else "")
               + (f":p{PROMPT_VERSION}" if args.summarizer == "claude" else ""))
        cached = cache.get(key)
        label = f"[{n}/{len(sessions)}] {s.start:%Y-%m-%d} {s.title[:60]!r}"
        if cached:
            result = cached
            print(f"{label} (cache)", file=sys.stderr)
        else:
            try:
                result = (summarize_claude(s, project, args.model, known=rules.projects())
                          if args.summarizer == "claude" else summarize_simple(s, project))
            except Exception as e:  # keep going, report at the end
                print(f"{label} FAILED: {e}", file=sys.stderr)
                previews.append({"session": s, "project": project, "error": str(e), "entries": []})
                continue
            cost += result.get("_cost_usd") or 0
            cache[key] = result
            _save_json(cache_path, cache)
            print(f"{label} -> {'skip' if result.get('skip') else len(result.get('entries') or [])}", file=sys.stderr)
        previews.append({"session": s, "project": project, "result": result,
                         "entries": to_entries(s, result, project, rules)})

    with open(out_jsonl, "w", encoding="utf-8") as f:
        for p in previews:
            for e in p["entries"]:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    out_md.write_text(_markdown(previews), encoding="utf-8")

    entries = sum(len(p["entries"]) for p in previews)
    skipped = sum(1 for p in previews if p.get("result", {}).get("skip"))
    failed = sum(1 for p in previews if p.get("error"))
    print(f"\n{entries} entries from {len(previews)} sessions ({skipped} skipped as private/no result, {failed} failed)",
          file=sys.stderr)
    if cost:
        print(f"claude -p cost (estimate): ${cost:.2f}", file=sys.stderr)
    print(f"review: {out_md}\nupload: worklog-import upload --file {out_jsonl}", file=sys.stderr)
    return 1 if failed else 0


def _markdown(previews: List[Dict[str, Any]]) -> str:
    lines = ["# Import preview", "",
             "Check the entries; to leave something out, delete its line in the .jsonl file "
             "or set `\"skip\": true` there.", ""]
    for p in previews:
        s: Session = p["session"]
        head = f"## {s.start.astimezone():%Y-%m-%d %H:%M} · {s.title}"
        lines += [head, "", f"`{s.external_base}` · {len(s.turns)} prompts · rule project: `{p['project']}`"
                  + (f" · `{s.cwd}`" if s.cwd else ""), ""]
        if p.get("error"):
            lines += [f"**Error:** {p['error']}", ""]
            continue
        result = p.get("result") or {}
        if result.get("skip"):
            lines += [f"_skipped: {result.get('skip_reason') or 'private or no result'}_", ""]
            continue
        if result.get("_redactions"):
            lines += [f"_{result['_redactions']} secret(s) redacted before summarizing_", ""]
        for e in p["entries"]:
            extra = []
            if e.get("duration_min"):
                extra.append(f"{e['duration_min']} min")
            if e.get("tags"):
                extra.append(", ".join(e["tags"]))
            local = parse_ts(e["ts"])
            when = local.astimezone().strftime("%Y-%m-%d %H:%M") if local else e["ts"][:16]
            lines.append(f"- **{e['project']}** · {when} · {e['summary']}"
                         + (f" ({'; '.join(extra)})" if extra else ""))
            if e.get("details"):
                lines.append(f"  - {e['details']}")
        lines.append("")
    return "\n".join(lines)


def cmd_upload(args: argparse.Namespace) -> int:
    url = args.url or os.environ.get("WORKLOG_URL")
    if not url:
        raise SystemExit("error: --url or $WORKLOG_URL is required (e.g. https://worklog.example.com)")
    token = os.environ.get("WORKLOG_TOKEN")
    if args.token_file:
        token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("error: set $WORKLOG_TOKEN or use --token-file")
    entries = []
    with open(args.file, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError as err:
                raise SystemExit(f"error: {args.file}:{n}: {err}")
            if e.pop("skip", False):
                continue
            entries.append({k: v for k, v in e.items() if not k.startswith("_")})
    print(f"{len(entries)} entries to {url}" + (" (update)" if args.update else ""), file=sys.stderr)
    if args.dry_run:
        return 0
    result = upload(url, token, entries, update=args.update)
    print(json.dumps({k: (v if k != "errors" else len(v)) for k, v in result.items()}))
    for err in result["errors"]:
        print(f"  error {err.get('external_id')}: {err.get('error')}", file=sys.stderr)
    return 1 if result["errors"] else 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="worklog-import", description="Import earlier AI sessions into the work log.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("prepare", help="extract, summarize and write a preview")
    pr.add_argument("--source", choices=["claude-code", "claude-ai", "copilot"], default="claude-code")
    pr.add_argument("--path", help="claude-code: projects folder (default ~/.claude/projects); "
                                   "claude-ai: export ZIP/folder/json; copilot: activity CSV")
    pr.add_argument("--tz", default="UTC", help="copilot: time zone of the CSV times (default UTC)")
    pr.add_argument("--gap-hours", type=float, default=6.0,
                    help="copilot: start a new session after a pause this long within one title (default 6)")
    pr.add_argument("--since", help="only sessions that ended on/after this date")
    pr.add_argument("--until", help="only sessions that started before this date")
    pr.add_argument("--rules", help="import-rules.yaml (or .json)")
    pr.add_argument("--summarizer", choices=["claude", "none"], default="claude")
    pr.add_argument("--model", help="model for claude -p (default: your Claude Code default)")
    pr.add_argument("--limit", type=int, help="only the oldest N sessions (for a trial run)")
    pr.add_argument("--exclude", nargs="*", help="session ids (or prefixes) to leave out")
    pr.add_argument("--min-age-hours", type=float, default=2.0,
                    help="skip sessions active in the last N hours, they may still be running (default 2)")
    pr.add_argument("--cache", default="import-cache.json")
    pr.add_argument("--out", default="import-preview", help="output prefix (default import-preview)")
    pr.set_defaults(func=cmd_prepare)

    up = sub.add_parser("upload", help="upload a reviewed preview")
    up.add_argument("--file", default="import-preview.jsonl")
    up.add_argument("--url", help="server base URL (default $WORKLOG_URL)")
    up.add_argument("--token-file", help="file with the token (default $WORKLOG_TOKEN)")
    up.add_argument("--update", action="store_true", help="overwrite entries with known external ids")
    up.add_argument("--dry-run", action="store_true")
    up.set_defaults(func=cmd_upload)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
