"""stdio transport: the client starts the server as a subprocess (e.g. docker run -i)."""

import os
import subprocess
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from worklog.db import Database


def transport(tmp_path, **env):
    return StdioTransport(command=sys.executable, args=["-m", "worklog.server", "--stdio"],
                          env={**os.environ, "WORKLOG_DB": str(tmp_path / "w.db"), **env})


async def test_stdio_logs_with_configured_identity(tmp_path):
    async with Client(transport(tmp_path, WORKLOG_OWNER="alice", WORKLOG_AGENT="claude-desktop")) as c:
        await c.call_tool("log_entry", {"project": "test", "summary": "über stdio"})
        listed = (await c.call_tool("list_entries", {})).structured_content["result"]
        assert [(e["agent"], e["summary"]) for e in listed] == [("claude-desktop", "über stdio")]
    db = Database(tmp_path / "w.db")
    assert [e.owner for e in db.list_entries(owner="alice")] == ["alice"]
    assert db.list_entries(owner="bob") == []          # same separation as over HTTP
    db.close()


def test_stdio_needs_an_owner(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("WORKLOG_OWNER", "WORKLOG_AGENT")}
    r = subprocess.run([sys.executable, "-m", "worklog.server", "--stdio"], input="", capture_output=True, text=True,
                       env={**env, "WORKLOG_DB": str(tmp_path / "w.db")}, timeout=30)
    assert r.returncode != 0 and "WORKLOG_OWNER" in r.stderr and r.stdout == ""
    r = subprocess.run([sys.executable, "-m", "worklog.server", "--stdio"], input="", capture_output=True, text=True,
                       env={**env, "WORKLOG_DB": str(tmp_path / "w.db"), "WORKLOG_OWNER": "a", "WORKLOG_AGENT": "bad name"},
                       timeout=30)
    assert r.returncode != 0 and "WORKLOG_AGENT" in r.stderr


def test_stdout_stays_clean_for_the_protocol(tmp_path):
    """Everything on stdout must be JSON-RPC; logs go to stderr."""
    init = ('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",'
            '"capabilities":{},"clientInfo":{"name":"t","version":"1"}}}\n')
    r = subprocess.run([sys.executable, "-m", "worklog.server", "--stdio"], input=init, capture_output=True, text=True,
                       env={**os.environ, "WORKLOG_DB": str(tmp_path / "w.db"), "WORKLOG_OWNER": "alice"}, timeout=30)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert lines and all(l.startswith('{"jsonrpc"') for l in lines), r.stdout[:300]
    assert '"starting"' in r.stderr
