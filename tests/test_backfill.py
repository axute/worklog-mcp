"""Logging work afterwards: ts on log_entry, log_entries, the backfill prompt, ts in the REST API."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from worklog.auth import create_token
from worklog.times import work_time

BERLIN = ZoneInfo("Europe/Berlin")


def test_work_time():
    now = datetime(2026, 11, 19, 10, tzinfo=BERLIN)
    assert work_time(None, BERLIN, now) is None and work_time(" ", BERLIN, now) is None
    assert work_time("2025-03-14", BERLIN, now).isoformat() == "2025-03-14T12:00:00+01:00"     # date = noon
    assert work_time("2025-07-01T16:30", BERLIN, now).isoformat() == "2025-07-01T16:30:00+02:00"
    assert work_time("2025-07-01T14:30:00Z", BERLIN, now).hour == 14
    assert work_time("2026-11-19T10:04", BERLIN, now)                                         # small clock skew
    for bad in ("2026-11-20", "2026-11-19T11:00", "1980-01-01", "gestern"):
        with pytest.raises(ValueError):
            work_time(bad, BERLIN, now)


async def test_log_entry_with_ts(server, db):
    token = create_token(db, "alice", "laptop", ["read", "write"])
    async with Client(server + "/mcp", auth=token) as c:
        r = (await c.call_tool("log_entry", {"project": "alt", "summary": "Shop aufgesetzt", "ts": "2025-03-14"})).structured_content
        assert r["ts"] == "2025-03-14T12:00:00+01:00"
        with pytest.raises(ToolError):
            await c.call_tool("log_entry", {"project": "alt", "summary": "Zukunft", "ts": "2099-01-01"})
    e = db.get_own_entry(owner="alice", entry_id=r["id"])
    assert e.created_at - e.ts > timedelta(days=300)                    # created_at shows it was logged later


async def test_log_entries(server, db):
    token = create_token(db, "alice", "laptop", ["read", "write"])
    db.add_entry(owner="alice", agent="laptop", source="tool", project="shop", summary="Zahlungsanbieter eingebunden",
                 ts=datetime(2025, 4, 2, 15, tzinfo=BERLIN))
    items = [
        {"project": "shop", "summary": "Shop-Grundgerüst aufgesetzt", "ts": "2025-03-14T10:00", "tags": ["backfill"]},
        {"project": "shop", "summary": "Zahlungsanbieter eingebunden", "ts": "2025-04-02T16:00", "duration_min": 120},
        {"project": "shop", "summary": "Versandkosten nach Gewicht", "ts": "2025-04-20"},
    ]
    async with Client(server + "/mcp", auth=token) as c:
        r = (await c.call_tool("log_entries", {"entries": items})).structured_content
        assert r["created"] == 3 and "hint" in r
        assert [("possible_duplicate_of" in e) for e in r["entries"]] == [False, True, False]
        # all checked before any is written: one bad entry -> nothing new
        before = len(db.list_entries(owner="alice", limit=500))
        with pytest.raises(ToolError):
            await c.call_tool("log_entries", {"entries": [items[0], {"project": "shop", "summary": "x", "ts": "2099-01-01"}]})
        with pytest.raises(ToolError):
            await c.call_tool("log_entries", {"entries": [{"project": " ", "summary": "x"}]})
        assert len(db.list_entries(owner="alice", limit=500)) == before
        listed = (await c.call_tool("list_entries", {"project": "shop", "limit": 10})).structured_content["result"]
        assert listed[-1]["summary"] == "Shop-Grundgerüst aufgesetzt" and "created_at" in listed[-1]
    reader = create_token(db, "alice", "reader", ["read"])
    async with Client(server + "/mcp", auth=reader) as c:
        with pytest.raises(ToolError):
            await c.call_tool("log_entries", {"entries": items[:1]})            # needs write


async def test_backfill_prompt(server, db):
    token = create_token(db, "alice", "laptop", ["read"])
    async with Client(server + "/mcp", auth=token) as c:
        r = await c.get_prompt("backfill_project", {"project": "shop", "since": "2025-01-01", "language": "German"})
        text = r.messages[0].content.text
        assert "list_entries with project='shop'" in text and "log_entries" in text and "git log" in text
        assert "from 2025-01-01 on" in text and "German" in text and "Never invent" in text


def test_rest_api_ts(server, db):
    h = {"Authorization": f"Bearer {create_token(db, 'alice', 'hook', ['write'])}"}
    r = httpx.post(server + "/api/entries", json={"project": "p", "summary": "alt", "ts": "2025-05-05T08:00"}, headers=h)
    assert r.status_code == 201 and r.json()["ts"] == "2025-05-05T08:00:00+02:00"
    assert httpx.post(server + "/api/entries", json={"project": "p", "summary": "x", "ts": "2099-01-01"},
                      headers=h).status_code == 400
    assert httpx.post(server + "/api/entries", json={"project": "p", "summary": "x", "ts": 5}, headers=h).status_code == 400
