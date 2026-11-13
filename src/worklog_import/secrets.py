"""Remove secrets from text before it leaves the machine.

Transcripts often contain pasted tokens and passwords. Everything that looks
like one is replaced with [REDACTED:<kind>]. Errs on the side of removing too much.
"""

from __future__ import annotations

import re
from typing import List, Tuple

PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("worklog-token", re.compile(r"\bwl_[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("basic-auth-url", re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")),
    # key = value / key: value for typical secret names
    ("assignment", re.compile(
        r"(?i)\b((?:[a-z0-9_.-]*?)(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
        r"client[_-]?secret|private[_-]?key|auth[_-]?token|credential)s?[a-z0-9_.-]*)"
        r"(\s*[:=]\s*)(['\"]?)(?!\[REDACTED)([^\s'\"]{6,})\3"
    )),
    # long random-looking strings (hex >= 32, base64-ish >= 40 with mixed classes)
    ("hex", re.compile(r"\b[a-f0-9]{40,}\b")),
    ("random", re.compile(r"\b(?=[A-Za-z0-9+/_-]*[0-9])(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*[a-z])[A-Za-z0-9+/_-]{40,}={0,2}")),
]


def redact(text: str) -> Tuple[str, int]:
    """Return (cleaned text, number of replacements)."""
    if not text:
        return text, 0
    count = 0

    def sub(kind: str, m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        if kind == "bearer":
            return f"{m.group(1)} [REDACTED:{kind}]"
        if kind == "basic-auth-url":
            return f"{m.group(1)}[REDACTED:{kind}]@"
        if kind == "assignment":
            return f"{m.group(1)}{m.group(2)}[REDACTED:secret]"
        return f"[REDACTED:{kind}]"

    for kind, pattern in PATTERNS:
        text = pattern.sub(lambda m, k=kind: sub(k, m), text)
    return text, count
