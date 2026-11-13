"""Parsers: raw session data -> Session records (deterministic, no AI)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


@dataclass
class Turn:
    ts: datetime
    prompt: str
    reply: str = ""


@dataclass
class Session:
    source: str            # "claude-code" | "claude-ai"
    session_id: str
    start: datetime
    end: datetime
    title: str = ""
    cwd: str = ""
    branch: str = ""
    turns: List[Turn] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)

    @property
    def external_base(self) -> str:
        return f"{self.source}:{self.session_id}"

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.external_base.encode())
        h.update(self.end.isoformat().encode())
        h.update(str(len(self.turns)).encode())
        return h.hexdigest()[:16]


def parse_ts(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        # Python 3.9 does not accept fractional seconds with != 3/6 digits
        try:
            head, _, tail = v.partition(".")
            frac, sign, off = "", "", ""
            for i, ch in enumerate(tail):
                if ch in "+-":
                    frac, sign, off = tail[:i], ch, tail[i + 1:]
                    break
            else:
                frac = tail
            dt = datetime.fromisoformat(head + ("." + (frac + "000000")[:6] if frac else "") + (sign + off if sign else ""))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ Claude Code

def _is_real_prompt(entry: Dict[str, Any]) -> Optional[str]:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if all(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    else:
        return None
    text = text.strip()
    if not text or text.startswith("<") or text.startswith("This session is being continued"):
        return None
    return text


def parse_claude_code_file(path: Path) -> Optional[Session]:
    """One JSONL transcript -> Session, or None when it has no real prompt."""
    turns: List[Turn] = []
    files: List[str] = []
    commands: List[str] = []
    stamps: List[datetime] = []
    cwd = branch = ""
    session_id = path.stem
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ts = parse_ts(e.get("timestamp"))
            if ts:
                stamps.append(ts)
            cwd = cwd or e.get("cwd") or ""
            branch = e.get("gitBranch") or branch
            prompt = _is_real_prompt(e)
            if prompt is not None and ts:
                turns.append(Turn(ts=ts, prompt=prompt))
                continue
            if e.get("type") != "assistant":
                continue
            content = (e.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and turns:
                    turns[-1].reply = c.get("text", "")  # keep the last text of the turn
                elif c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    if c.get("name") in EDIT_TOOLS and inp.get("file_path") and inp["file_path"] not in files:
                        files.append(str(inp["file_path"]))
                    elif c.get("name") == "Bash" and inp.get("command"):
                        commands.append(str(inp["command"]).strip().splitlines()[0][:200])
    if not turns or not stamps:
        return None
    return Session(source="claude-code", session_id=session_id, start=min(stamps), end=max(stamps),
                   title=turns[0].prompt.splitlines()[0][:120], cwd=cwd, branch=branch,
                   turns=turns, files=files, commands=commands)


def claude_code_sessions(root: Path) -> Iterator[Session]:
    """All sessions below ~/.claude/projects (subagent transcripts are ignored)."""
    for path in sorted(root.glob("*/*.jsonl")):
        if "subagents" in path.parts:
            continue
        s = parse_claude_code_file(path)
        if s:
            yield s


# ------------------------------------------------------------------ claude.ai export

def _load_claude_ai(path: Path) -> List[Dict[str, Any]]:
    if path.is_dir():
        path = path / "conversations.json"
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            if not name:
                raise ValueError(f"{path}: no conversations.json in the archive")
            with z.open(name) as f:
                return json.load(io.TextIOWrapper(f, encoding="utf-8"))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _message_text(m: Dict[str, Any]) -> str:
    text = m.get("text") or ""
    if not text and isinstance(m.get("content"), list):
        text = "\n".join(c.get("text", "") for c in m["content"] if isinstance(c, dict) and c.get("type") == "text")
    return text.strip()


def claude_ai_sessions(path: Path) -> Iterator[Session]:
    """Conversations from the claude.ai data export (ZIP, directory or conversations.json)."""
    for conv in _load_claude_ai(path):
        if not isinstance(conv, dict):
            continue
        uuid = conv.get("uuid") or conv.get("id")
        messages = conv.get("chat_messages") or conv.get("messages") or []
        turns: List[Turn] = []
        stamps: List[datetime] = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            ts = parse_ts(m.get("created_at")) or parse_ts(conv.get("created_at"))
            if ts:
                stamps.append(ts)
            text = _message_text(m)
            sender = (m.get("sender") or m.get("role") or "").lower()
            if sender in ("human", "user") and text and ts:
                turns.append(Turn(ts=ts, prompt=text))
            elif sender == "assistant" and turns:
                turns[-1].reply = text
        if not uuid or not turns:
            continue
        yield Session(source="claude-ai", session_id=str(uuid), start=min(stamps), end=max(stamps),
                      title=(conv.get("name") or turns[0].prompt.splitlines()[0])[:120], turns=turns)


# ------------------------------------------------------------------ Microsoft Copilot (CSV)

def copilot_sessions(path: Path, tz: str = "UTC", gap_hours: float = 6.0) -> Iterator[Session]:
    """Copilot activity export (CSV: Conversation, Time, Author, Message).

    The export has no conversation id and no time zone: rows are grouped by
    title and split where the pause is longer than gap_hours; times are read
    in `tz`. The id is derived from title and start time, so re-imports of the
    same export find the same ids."""
    import csv
    from collections import defaultdict
    from datetime import timedelta
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception as e:  # zoneinfo missing or unknown name
        raise ValueError(f"unknown time zone '{tz}': {e}") from e

    rows_by_title: Dict[str, List[Any]] = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            title = (row.get("Conversation") or "").strip()
            raw = (row.get("Time") or "").strip()
            try:
                when = datetime.fromisoformat(raw)
            except ValueError:
                continue
            when = (when if when.tzinfo else when.replace(tzinfo=zone)).astimezone(timezone.utc)
            author = (row.get("Author") or "").strip().lower()
            rows_by_title[title].append((when, 0 if author in ("human", "user") else 1, author, row.get("Message") or ""))

    gap = timedelta(hours=gap_hours)
    for title, rows in rows_by_title.items():
        rows.sort(key=lambda r: (r[0], r[1]))
        groups: List[List[Any]] = [[rows[0]]]
        for prev, cur in zip(rows, rows[1:]):
            if cur[0] - prev[0] > gap:
                groups.append([])
            groups[-1].append(cur)
        for group in groups:
            turns: List[Turn] = []
            for when, _, author, text in group:
                text = text.strip()
                if author in ("human", "user") and text:
                    turns.append(Turn(ts=when, prompt=text))
                elif turns and text:
                    turns[-1].reply = text
            if not turns:
                continue
            start = group[0][0]
            sid = hashlib.sha1(f"{title}|{start.isoformat()}".encode("utf-8")).hexdigest()[:16]
            yield Session(source="copilot", session_id=sid, start=start, end=group[-1][0],
                          title=(title or turns[0].prompt.splitlines()[0])[:120], turns=turns)


def default_claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def filter_sessions(sessions: Iterable[Session], since: Optional[datetime], until: Optional[datetime],
                    min_age_hours: float, exclude: List[str]) -> Iterator[Session]:
    now = datetime.now(timezone.utc)
    for s in sessions:
        if since and s.end < since:
            continue
        if until and s.start >= until:
            continue
        if min_age_hours and (now - s.end).total_seconds() < min_age_hours * 3600:
            continue  # probably still running
        if any(s.session_id.startswith(x) for x in exclude):
            continue
        yield s
