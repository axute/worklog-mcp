"""Turn a Session into work log entries.

"claude": `claude -p` with a fixed prompt and a JSON schema, isolated from the
user's setup (no tools, no MCP servers, no hooks, no session file), so the
summarizer itself can never write to the work log.
"none":   deterministic fallback, one entry per session from the first prompt.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .secrets import redact
from .sources import Session

MAX_INPUT_CHARS = 60_000
MAX_ENTRIES = 8

SCHEMA = {
    "type": "object",
    "properties": {
        "skip": {"type": "boolean", "description": "true for private or non-work conversations and trivial sessions"},
        "skip_reason": {"type": "string"},
        "entries": {
            "type": "array",
            "maxItems": MAX_ENTRIES,
            "items": {
                "type": "object",
                "properties": {
                    "ts": {"type": "string", "description": "ISO timestamp of the prompt where this result was reached, copied from the input"},
                    "project": {"type": "string"},
                    "summary": {"type": "string", "maxLength": 300},
                    "details": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "duration_min": {"type": "integer", "minimum": 0},
                },
                "required": ["ts", "summary"],
            },
        },
    },
    "required": ["skip", "entries"],
}

PROMPT = """\
You write entries for a personal work log from a past AI chat session.

Rules:
- One entry per finished result (feature, fix, configuration change, research result,
  decision), at most {max_entries}. A short session usually has exactly one entry.
- summary: one sentence, max. 300 characters, what is different now (the result,
  not the process). details: important commands, files, decisions and reasons.
- Write in the language the user wrote in.
- ts: copy the timestamp of the prompt in which the result was reached.
- duration_min: rough estimate from the timestamps.
- project: short project name, lowercase with hyphens (e.g. "home-assistant").
  Known projects: {known}. Use one of them whenever it fits; the session's suggested
  project is "{project}". If none fits, invent a short new lowercase name for the
  topic. Use "unsortiert" only if the topic is really unclear.
- tags: up to 5 short lowercase tags.
- Never include secrets, tokens, passwords, personal data of others. Text marked
  [REDACTED] stays redacted.
- Set skip=true (and entries=[]) for private or non-work conversations, small talk,
  and sessions without a result.

Session ({source}, {start} - {end}):
{body}
"""


def session_text(s: Session) -> str:
    parts = []
    if s.title:
        parts.append(f"Title: {s.title}")
    if s.cwd:
        parts.append(f"Working directory: {s.cwd}" + (f" (git branch {s.branch})" if s.branch else ""))
    for t in s.turns:
        parts.append(f"\n[{t.ts.isoformat()}] USER: {t.prompt[:3000]}")
        if t.reply:
            parts.append(f"ASSISTANT (last message of the turn): {t.reply[:1500]}")
    if s.files:
        parts.append("\nChanged files: " + ", ".join(s.files[:60]))
    if s.commands:
        parts.append("Commands (sample): " + " | ".join(s.commands[:40]))
    text = "\n".join(parts)
    if len(text) > MAX_INPUT_CHARS:
        half = MAX_INPUT_CHARS // 2
        text = text[:half] + "\n[... middle of the session omitted ...]\n" + text[-half:]
    return text


def summarize_claude(s: Session, project: str, model: Optional[str] = None, timeout: int = 300,
                     known: Optional[List[str]] = None) -> Dict[str, Any]:
    body, redactions = redact(session_text(s))
    prompt = PROMPT.format(max_entries=MAX_ENTRIES, project=project, known=", ".join(known or []) or "none",
                           source=s.source,
                           start=s.start.isoformat(), end=s.end.isoformat(), body=body)
    cmd = ["claude", "-p", "--tools", "", "--strict-mcp-config", "--settings", '{"disableAllHooks": true}',
           "--disable-slash-commands", "--no-session-persistence", "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA)]
    if model:
        cmd += ["--model", model]
    workdir = Path(tempfile.gettempdir()) / "worklog-import-claude"
    workdir.mkdir(exist_ok=True)
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=workdir)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-500:]}")
    out = json.loads(proc.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p error: {str(out.get('result'))[:500]}")
    result = out.get("structured_output")
    if not isinstance(result, dict):
        result = json.loads(out.get("result") or "{}")
    result["_cost_usd"] = out.get("total_cost_usd")
    result["_redactions"] = redactions
    return result


def summarize_simple(s: Session, project: str) -> Dict[str, Any]:
    first = s.turns[0].prompt.splitlines()[0]
    summary, _ = redact(first[:300])
    details = []
    if len(s.turns) > 1:
        details.append(f"{len(s.turns)} prompts")
    if s.files:
        details.append("files: " + ", ".join(Path(f).name for f in s.files[:15]))
    detail_text, _ = redact("; ".join(details))
    minutes = max(1, int((s.end - s.start).total_seconds() // 60))
    return {"skip": False, "entries": [{
        "ts": s.start.isoformat(), "project": project, "summary": summary,
        "details": detail_text, "tags": [s.source], "duration_min": min(minutes, 600),
    }]}


def to_entries(s: Session, result: Dict[str, Any], project: str, rules: Any = None) -> List[Dict[str, Any]]:
    """Summarizer output -> upload-ready entries with stable external ids.

    Project per entry: a rule matching the entry text wins, then the model's
    project (normalized), then the session's rule project."""
    from .rules import DEFAULT_PROJECT, normalize_project
    if result.get("skip"):
        return []
    entries = []
    for i, e in enumerate(result.get("entries") or []):
        summary, _ = redact(str(e.get("summary") or "").strip()[:300])
        if not summary:
            continue
        details, _ = redact(str(e.get("details") or "").strip())
        ts = e.get("ts") or s.start.isoformat()
        try:
            datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            ts = s.start.isoformat()
        proj = (rules.match_text(summary + "\n" + details) if rules else None) \
            or normalize_project(str(e.get("project") or "")) or project
        if proj == DEFAULT_PROJECT and project != DEFAULT_PROJECT:
            proj = project
        entries.append({
            "external_id": f"{s.external_base}:{i}",
            "ts": ts,
            "project": proj,
            "summary": summary,
            "details": details or None,
            "tags": [str(t).strip().lower() for t in (e.get("tags") or []) if str(t).strip()][:5],
            "duration_min": e.get("duration_min") if isinstance(e.get("duration_min"), int) else None,
            "session_id": s.session_id,
        })
    return entries
