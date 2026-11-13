"""End to end over HTTP: auth, tools, health route."""

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_healthz_without_token(server):
    r = httpx.get(server + "/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


@pytest.mark.parametrize("auth", [None, "Bearer wrong", "Bearer wl_doesnotexist", "Basic dXNlcjpwdw=="])
def test_mcp_rejects_missing_or_bad_token(server, tokens, auth):
    headers = dict(HEADERS)
    if auth:
        headers["Authorization"] = auth
    r = httpx.post(server + "/mcp", json=INIT, headers=headers)
    assert r.status_code == 401
    assert "bearer" in r.headers.get("www-authenticate", "").lower()


def test_mcp_rejects_revoked_token(server, db, tokens):
    db.revoke_token("claude-code-laptop")
    r = httpx.post(server + "/mcp", json=INIT, headers={**HEADERS, "Authorization": f"Bearer {tokens['rw']}"})
    assert r.status_code == 401


async def test_tools_listed(server, tokens):
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        names = {t.name for t in await c.list_tools()}
    assert {"log_entry", "list_entries"} <= names


async def test_log_and_list(server, tokens):
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        res = await c.call_tool("log_entry", {
            "project": "website", "summary": "Startseite überarbeitet",
            "details": "npm run build", "tags": ["backup"], "duration_min": 20,
        })
        assert res.data["id"] == 1
        assert res.data["ts"].endswith(("+01:00", "+02:00"))  # Europe/Berlin
        listed = (await c.call_tool("list_entries", {"project": "website"})).data
    assert len(listed) == 1
    assert listed[0]["agent"] == "claude-code-laptop"  # from the token, not from the call
    assert listed[0]["source"] == "tool"
    assert listed[0]["tags"] == ["backup"]


async def test_agent_cannot_be_spoofed(server, tokens):
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        with pytest.raises(ToolError):
            await c.call_tool("log_entry", {"project": "p", "summary": "x", "agent": "someone-else"})


async def test_scopes(server, tokens):
    async with Client(server + "/mcp", auth=tokens["r"]) as c:
        with pytest.raises(ToolError, match="write"):
            await c.call_tool("log_entry", {"project": "p", "summary": "x"})
        assert (await c.call_tool("list_entries", {})).data == []
    async with Client(server + "/mcp", auth=tokens["w"]) as c:
        assert (await c.call_tool("log_entry", {"project": "p", "summary": "x"})).data["id"] == 1
        with pytest.raises(ToolError, match="read"):
            await c.call_tool("list_entries", {})


async def test_validation_errors_reach_the_client(server, tokens):
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        with pytest.raises(ToolError, match="300"):
            await c.call_tool("log_entry", {"project": "p", "summary": "x" * 301})
        with pytest.raises(ToolError):
            await c.call_tool("list_entries", {"since": "gestern"})


async def test_local_dates_in_filters(server, db, tokens):
    from datetime import datetime, timezone
    # 23:30 UTC on 12 Nov is 00:30 on 13 Nov in Berlin
    db.add_entry(agent="a", source="tool", project="p", summary="late", ts=datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc))
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        on_13th = (await c.call_tool("list_entries", {"since": "2026-11-13", "until": "2026-11-14"})).data
    assert [e["summary"] for e in on_13th] == ["late"]
    assert on_13th[0]["ts"] == "2026-11-13T00:30:00+01:00"
