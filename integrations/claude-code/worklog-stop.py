#!/usr/bin/env python3
"""Claude Code Stop hook: remind Claude to call worklog log_entry after changes.

Install: copy to ~/.claude/hooks/worklog-stop.py and register it in
~/.claude/settings.json (see the README, "Setup guide", step 7).

Looks only at the current turn (since the last real user prompt). If that turn
changed something (Edit/Write/NotebookEdit, or a Bash command that writes) and
no worklog log_entry came after the last change, it blocks once with a
reminder. Never blocks twice in a row (stop_hook_active), never blocks when the
worklog tool was not used/available, and fails open on any error.
"""

import json
import re
import sys

LOG_TOOL = re.compile(r"^mcp__.*worklog.*__log_entry$")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Bash commands that change something (files, containers, git, packages, services)
WRITE_BASH = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:"
    r"rm|mv|cp|mkdir|rmdir|touch|chmod|chown|ln|install|tee|truncate|dd|"
    r"sed\s+(?:-\S*\s+)*-i|perl\s+-\S*i|"
    r"git\s+(?:commit|push|merge|rebase|reset|checkout|switch|tag|init|rm|mv|apply|stash|cherry-pick|revert)|"
    r"docker\s+(?:run|rm|rmi|build|create|start|stop|restart|kill|network\s+(?:create|rm)|volume\s+(?:create|rm)|compose\s+(?:up|down|build|rm|restart|stop|start))|"
    r"plugin\s+(?:install|remove|update)|"
    r"(?:pip|npm|apt|apt-get|installpkg|upgradepkg|removepkg)\s+(?:install|uninstall|remove|i\b)|"
    r"gh\s+(?:release|repo|pr|issue)\s+(?:create|edit|delete|merge|close)|"
    r"crontab|systemctl\s+(?:start|stop|restart|enable|disable)"
    r")\b"
)
# file writes inside inline scripts (python/node heredocs, -c strings)
SCRIPT_WRITE = re.compile(
    r"open\([^)]*,\s*['\"](?:w|a|x|r\+|wb|ab)['\"]|\.write_text\(|\.write_bytes\(|\.unlink\(|"
    r"os\.(?:remove|rename|replace|makedirs|mkdir)\(|shutil\.(?:copy|move|rmtree)|writeFileSync|appendFileSync"
)
REDIRECT = re.compile(r"(?<![0-9&])>>?\s*(?!/dev/null|&)[^\s|;&]")


def is_real_prompt(entry):
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return not content.startswith("<")
    if isinstance(content, list):
        return any(isinstance(c, dict) and c.get("type") == "text" for c in content) and not all(
            isinstance(c, dict) and c.get("type") == "tool_result" for c in content
        )
    return False


def tool_uses(entry):
    if entry.get("type") != "assistant":
        return []
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]


def shell_part(cmd):
    """Only what the shell itself runs: drop heredoc bodies and quoted strings
    (inline scripts like python -c "a > b" are not redirects)."""
    lines = cmd.split("\n")
    out, skip_until = [], None
    for line in lines:
        if skip_until is not None:
            if line.strip() == skip_until:
                skip_until = None
            continue
        out.append(line)
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if m:
            skip_until = m.group(1)
    text = "\n".join(out)
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", text)


def changes_something(tool):
    name = tool.get("name", "")
    if name in EDIT_TOOLS:
        return True
    if name == "Bash":
        raw = str((tool.get("input") or {}).get("command", ""))
        cmd = shell_part(raw)
        return bool(WRITE_BASH.search(cmd) or REDIRECT.search(cmd) or SCRIPT_WRITE.search(raw))
    return False


def main():
    data = json.load(sys.stdin)
    if data.get("stop_hook_active"):
        return
    path = data.get("transcript_path")
    if not path:
        return
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue

    start = 0
    for i in range(len(entries) - 1, -1, -1):
        if is_real_prompt(entries[i]):
            start = i + 1
            break

    last_change = last_log = -1
    for i in range(start, len(entries)):
        for tool in tool_uses(entries[i]):
            if LOG_TOOL.match(tool.get("name", "")):
                last_log = i
            elif changes_something(tool):
                last_change = i

    if last_change >= 0 and last_log < last_change:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "Something was changed in this turn but not logged. If a work step is finished, "
                "record it now with worklog log_entry (rule from CLAUDE.md). If it was only an "
                "intermediate step, pure research, or worklog is not available, just reply without an entry."
            ),
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: never break a session because of this hook
