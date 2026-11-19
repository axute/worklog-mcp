"""Hand-written entries, timesheet, project details, standup prompt, notifications, demo data, migration 6."""

import dataclasses
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastmcp import Client

from worklog.auth import create_token
from worklog.cli import main
from worklog.db import MIGRATIONS, Database
from worklog.report import timesheet, timesheet_csv

BERLIN = ZoneInfo("Europe/Berlin")
SECRET = "s" * 40


def add(db, project="website", minutes=30, day=10, hour=9, owner="alice", **kw):
    return db.add_entry(owner=owner, agent="laptop", source="tool", project=project, summary=f"{project} {day} {hour}",
                        duration_min=minutes, ts=datetime(2026, 11, day, hour, tzinfo=BERLIN), **kw)


# ------------------------------------------------------------------ timesheet

def test_timesheet_days_weeks_months(db):
    add(db, day=9, minutes=50)                      # Monday, week 46
    add(db, day=9, hour=14, minutes=20)
    add(db, project="infra", day=11, minutes=10)
    add(db, day=17, minutes=None)                   # week 47, no duration
    entries, _ = db.query_entries(owner="alice", limit=None)
    start, end = datetime(2026, 11, 9, tzinfo=BERLIN), datetime(2026, 11, 16, tzinfo=BERLIN)
    s = timesheet(entries, BERLIN, "day", start, end)
    assert [c["key"] for c in s["columns"]] == [f"2026-11-{d:02d}" for d in range(9, 16)]       # no gaps
    assert s["rows"][0] == {"project": "website", "minutes": [70, 0, 0, 0, 0, 0, 0], "entries": 2, "total": 70}
    assert s["totals"][2] == 10 and s["total"] == 80
    w = timesheet(entries, BERLIN, "week")
    assert [c["key"] for c in w["columns"]] == ["2026-W46", "2026-W47"]
    assert w["rows"][0]["minutes"] == [70, 0] and w["rows"][0]["entries"] == 3
    assert timesheet(entries, BERLIN, "month")["columns"] == [{"key": "2026-11", "start": "2026-11-09"}]
    r = timesheet(entries, BERLIN, "day", start, end, round_to=15)
    assert r["rows"][0]["minutes"][0] == 75 and r["rows"][1]["minutes"][2] == 15               # each cell rounded up
    assert timesheet_csv(r).splitlines()[0].startswith("project,2026-11-09")
    assert timesheet([], BERLIN, "week")["columns"] == []
    with pytest.raises(ValueError):
        timesheet(entries, BERLIN, "year")
    with pytest.raises(ValueError):
        timesheet(entries, BERLIN, "day", datetime(2020, 1, 1, tzinfo=BERLIN), end)            # > 400 columns


@pytest.fixture
def proxy(make_server, settings):
    return make_server(dataclasses.replace(settings, proxy_secret=SECRET))


def headers(user, server):
    return {"X-Worklog-Proxy": SECRET, "Remote-User": user, "Origin": server}


def test_timesheet_endpoint(proxy, db):
    add(db, day=9)
    add(db, owner="bob", day=9)
    with httpx.Client(base_url=proxy, headers=headers("alice", proxy)) as c:
        s = c.get("/ui/api/timesheet", params={"unit": "day", "since": "2026-11-09", "until": "2026-11-11"}).json()
        assert s["total"] == 30 and len(s["columns"]) == 2 and len(s["rows"]) == 1
        r = c.get("/ui/api/timesheet", params={"unit": "week", "format": "csv", "round": "60"})
        assert r.headers["content-type"].startswith("text/csv") and "timesheet-week-all.csv" in r.headers["content-disposition"]
        assert r.text.splitlines()[1] == "website,60,60,1.00"
        assert c.get("/ui/api/timesheet", params={"unit": "year"}).status_code == 400
        assert c.get("/ui/api/timesheet", params={"round": "x"}).status_code == 400


# ------------------------------------------------------------------ entries by hand

def test_new_entry_by_hand(proxy, db):
    writer = create_token(db, "alice", "laptop", ["read", "write"])
    reader = create_token(db, "alice", "reader", ["read"])
    with httpx.Client(base_url=proxy, headers=headers("alice", proxy)) as c:
        r = c.post("/ui/api/entries", json={"project": "garten", "summary": "Hecke geschnitten", "duration_min": 45,
                                            "ts": "2026-11-15T10:30", "tags": ["draußen"]})
        assert r.status_code == 201
        e = r.json()
        assert (e["source"], e["agent"], e["ts"]) == ("web", "web", "2026-11-15T10:30:00+01:00")
        assert c.post("/ui/api/entries", json={"project": "g", "summary": "x", "ts": "2099-01-01"}).status_code == 400
        assert c.post("/ui/api/entries", json={"project": "g", "summary": ""}).status_code == 400
        assert c.post("/ui/api/entries", json={"project": "g", "summary": "x", "owner": "bob"}).status_code == 400
    with httpx.Client(base_url=proxy, headers={"Origin": proxy}) as c:                  # token sign-in
        c.post("/ui/login", json={"token": writer})
        e = c.post("/ui/api/entries", json={"project": "garten", "summary": "Laub gerecht"}).json()
        assert e["agent"] == "laptop" and e["source"] == "web"                           # the token's name
        c.post("/ui/logout")
        c.post("/ui/login", json={"token": reader})
        assert c.post("/ui/api/entries", json={"project": "garten", "summary": "x"}).status_code == 403
    assert db.list_entries(owner="bob") == []


# ------------------------------------------------------------------ project details

def test_project_details(proxy, db):
    add(db)
    add(db, project="alt")
    add(db, owner="bob")
    with pytest.raises(KeyError):
        db.set_project_details(owner="alice", name="nope", description="x")
    with pytest.raises(ValueError):
        db.set_project_details(owner="alice", name="website", color="blue")
    with httpx.Client(base_url=proxy, headers=headers("alice", proxy)) as c:
        r = c.post("/ui/api/projects/details", json={"name": "website", "description": "Firmen-Website",
                                                     "customer": "Acme", "color": "#2A78D6"})
        assert r.json() == {"project": "website", "description": "Firmen-Website", "customer": "Acme",
                            "color": "#2a78d6", "archived": False}
        c.post("/ui/api/projects/details", json={"name": "alt", "archived": True})
        assert c.post("/ui/api/projects/details", json={"name": "alt", "archived": "ja"}).status_code == 400
        assert c.post("/ui/api/projects/details", json={"name": "nope"}).status_code == 404
        listed = {p["project"]: p for p in c.get("/ui/api/projects").json()["projects"]}
        assert listed["website"]["customer"] == "Acme" and listed["alt"]["archived"] is True
        facets = {p["name"]: p for p in c.get("/ui/api/facets").json()["projects"]}
        assert facets["alt"]["archived"] and facets["website"]["color"] == "#2a78d6"
        # details move along on rename; merging keeps the target's details
        c.post("/ui/api/projects/rename", json={"from": "website", "to": "web"})
        assert db.project_details(owner="alice")["web"]["customer"] == "Acme"
        c.post("/ui/api/projects/rename", json={"from": "alt", "to": "web"})
        assert db.project_details(owner="alice")["web"]["archived"] is False and "alt" not in db.project_details(owner="alice")
    assert db.project_details(owner="bob") == {}
    assert [p["project"] for p in db.list_projects(owner="alice", include_archived=False)] == ["web"]


async def test_list_projects_tool_shows_details(server, db):
    add(db)
    db.set_project_details(owner="alice", name="website", description="Firmen-Website", archived=True)
    token = create_token(db, "alice", "laptop", ["read"])
    async with Client(server + "/mcp", auth=token) as c:
        p = (await c.call_tool("list_projects", {})).structured_content["result"][0]
        assert p["description"] == "Firmen-Website" and p["archived"] is True


# ------------------------------------------------------------------ standup prompt

async def test_standup_prompt(server, db):
    token = create_token(db, "alice", "laptop", ["read"])
    async with Client(server + "/mcp", auth=token) as c:
        text = (await c.get_prompt("standup", {"language": "German"})).messages[0].content.text
        today = datetime.now(BERLIN).date()
        assert f"until='{today}'" in text and "summarize_period" in text and "German" in text
        assert "don't make up plans" in text


# ------------------------------------------------------------------ notifications

class Catcher(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        Catcher.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def catcher():
    Catcher.received = []
    srv = HTTPServer(("127.0.0.1", 0), Catcher)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"json://127.0.0.1:{srv.server_port}/hook"
    srv.shutdown()


def test_report_notification(tmp_path, catcher, capsys, monkeypatch):
    from worklog.report import period_bounds
    path = str(tmp_path / "w.db")
    db = Database(path)
    start, _, label = period_bounds("last-week", BERLIN)
    db.add_entry(owner="alice", agent="a", source="tool", project="website", summary="x", duration_min=90,
                 ts=start + timedelta(hours=10))
    db.close()
    monkeypatch.setenv("WORKLOG_NOTIFY", catcher)
    assert main(["--db", path, "report", "--owner", "alice", "--notify", "--dir", str(tmp_path)]) == 0
    msg = Catcher.received[0]
    assert msg["title"] == f"Worklog {label}" and "1.5 h" in msg["message"] and "**website**" in msg["message"]
    assert main(["--db", path, "notify-test", "--notify-url", "notavalidurl"]) == 1   # invalid target counts as failed
    monkeypatch.delenv("WORKLOG_NOTIFY")
    assert main(["--db", path, "notify-test"]) == 1                                     # no targets at all
    assert "WORKLOG_NOTIFY" in capsys.readouterr().err


# ------------------------------------------------------------------ demo data and migration 6

def test_demo_data(tmp_path, capsys):
    path = str(tmp_path / "w.db")
    assert main(["--db", path, "demo-data", "--owner", "demo", "--days", "30"]) == 0
    db = Database(path)
    entries, total = db.query_entries(owner="demo", limit=None)
    assert total > 20 and {e.project for e in entries} <= {"website-relaunch", "invoice-tool", "home-lab", "mobile-app", "docs"}
    assert all(e.ts <= datetime.now(timezone.utc) for e in entries)
    db.close()
    assert main(["--db", path, "demo-data", "--owner", "demo"]) == 1                    # only into an empty user


def test_migration_6_keeps_data(tmp_path):
    path = tmp_path / "v5.db"
    d = Database(path)
    d.add_entry(owner="alice", agent="a", source="hook", project="p", summary="vorher da")
    d.close()
    conn = sqlite3.connect(path)                     # roll the schema back to version 5
    conn.executescript("DROP TABLE projects; PRAGMA user_version = 5;")
    conn.close()
    d = Database(path)
    assert d.schema_version() == len(MIGRATIONS)
    assert d.search_entries("vorher", owner="alice")[0].source == "hook"
    d.add_entry(owner="alice", agent="web", source="web", project="p", summary="danach")
    assert len(d.search_entries("danach", owner="alice")) == 1
    d.close()
