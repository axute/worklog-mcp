"""Bulk upload to POST /api/entries/bulk (500 per request).

Uses urllib; falls back to curl when this Python has no SSL support.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List

CHUNK = 500


def _post(url: str, token: str, body: bytes) -> Dict[str, Any]:
    try:
        import ssl  # noqa: F401
        has_ssl = True
    except ImportError:
        has_ssl = False
    if url.startswith("https://") and not has_ssl:
        if not shutil.which("curl"):
            raise RuntimeError("this Python has no SSL support and curl is not installed")
        return _curl(url, token, body)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from e


def _curl(url: str, token: str, body: bytes) -> Dict[str, Any]:
    """curl with the token in a header file read from stdin, so it never shows up in `ps`."""
    import os
    import tempfile
    with tempfile.NamedTemporaryFile("wb", delete=False) as f:
        f.write(body)
        data_file = f.name
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-X", "POST", url, "-H", "Content-Type: application/json", "-H", "@-",
             "--data-binary", f"@{data_file}", "-w", "\n%{http_code}"],
            input=f"Authorization: Bearer {token}\n", capture_output=True, text=True, timeout=120,
        )
    finally:
        os.unlink(data_file)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip()}")
    text, _, status = proc.stdout.rpartition("\n")
    if not status.startswith("2"):
        raise RuntimeError(f"HTTP {status}: {text[:300]}")
    return json.loads(text)


def upload(base_url: str, token: str, entries: List[Dict[str, Any]], update: bool = False) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/api/entries/bulk" + ("?update=true" if update else "")
    total: Dict[str, Any] = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    for start in range(0, len(entries), CHUNK):
        chunk = entries[start:start + CHUNK]
        res = _post(url, token, json.dumps({"entries": chunk}, ensure_ascii=False).encode("utf-8"))
        for k in ("created", "updated", "skipped"):
            total[k] += res.get(k, 0)
        for err in res.get("errors", []):
            err = dict(err)
            idx = err.get("index")
            if isinstance(idx, int):
                err["external_id"] = chunk[idx].get("external_id")
                err["index"] = start + idx
            total["errors"].append(err)
    return total
