"""Correcting entries, renaming projects, exports, report prompts and the report command."""

import dataclasses
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from worklog.auth import create_token
from worklog.cli import main
from worklog.report import period_bounds, summary_line

BERLIN = ZoneInfo("Europe/Berlin")
SECRET = "s" * 40


@pytest.fixture
def proxy(make_server, settings):
    return make_server(dataclasses.replace(settings, proxy_secret=SECRET))


def as_user(user, server):
    return {"X-Worklog-Proxy": SECRET, "Remote-User": user, "Origin": server}


def add(db, owner="alice", agent="laptop", project="website", summary="Startseite gebaut", **kw):
    return db.add_entry(owner=owner, agent=agent, source="tool", project=project, summary=summary, **kw)


# ------------------------------------------------------------------ database

def test_update_entry(db):
    e = add(db, details="alt", tags=["a"], duration_min=5)
    u = db.update_entry(owner="alice", entry_id=e.id, project="web", tags=["X", "y"], duration_min=None)
    assert (u.project, u.summary, u.details, u.tags, u.duration_min) == ("web", "Startseite gebaut", "alt", ["x", "y"], None)
    assert db.update_entry(owner="alice", entry_id=e.id, details="").details is None
    assert db.search_entries("web", owner="alice")[0].id == e.id                 # full text index follows
    for bad in ({"summary": ""}, {"summary": "x" * 301}, {"project": " "}, {"duration_min": -1},
                {"duration_min": True}, {"tags": "a"}, {"source": "hook"}, {}):
        with pytest.raises(ValueError):
            db.update_entry(owner="alice", entry_id=e.id, **bad)
    with pytest.raises(KeyError):
        db.update_entry(owner="bob", entry_id=e.id, summary="fremd")             # other user
    with pytest.raises(KeyError):
        db.update_entry(owner="alice", entry_id=e.id, agent="phone", summary="x")  # other agent


def test_delete_and_rename(db):
    a, b = add(db), add(db, project="web", summary="Kontakt")
    add(db, owner="bob", project="website", summary="Bobs")
    with pytest.raises(KeyError):
        db.delete_entry(owner="bob", entry_id=a.id)
    assert db.rename_project(owner="alice", old="website", new="web") == 1      # merge into existing
    assert {p["project"] for p in db.list_projects(owner="alice")} == {"web"}
    assert {p["project"] for p in db.list_projects(owner="bob")} == {"website"}  # bob untouched
    with pytest.raises(KeyError):
        db.rename_project(owner="alice", old="nope", new="x")
    with pytest.raises(ValueError):
        db.rename_project(owner="alice", old="web", new=" ")
    db.delete_entry(owner="alice", entry_id=b.id)
    assert [e.id for e in db.list_entries(owner="alice")] == [a.id]
    assert db.search_entries("kontakt", owner="alice") == []


def test_period_bounds_and_summary():
    now = datetime(2026, 11, 18, 9, 0, tzinfo=BERLIN)                           # Wednesday, week 47
    s, e, label = period_bounds("last-week", BERLIN, now)
    assert (s.isoformat(), e.isoformat(), label) == ("2026-11-09T00:00:00+01:00", "2026-11-16T00:00:00+01:00", "2026-W46")
    assert period_bounds("this-week", BERLIN, now)[2] == "2026-W47"
    s, e, label = period_bounds("last-month", BERLIN, now)
    assert (s.date().isoformat(), e.date().isoformat(), label) == ("2026-10-01", "2026-11-01", "2026-10")
    jan = datetime(2027, 1, 5, tzinfo=BERLIN)
    assert period_bounds("last-month", BERLIN, jan)[2] == "2026-12"
    assert period_bounds("month", BERLIN, jan, ago=13)[2] == "2025-12"
    assert period_bounds("last-week", BERLIN, jan)[2] == "2026-W53"
    with pytest.raises(ValueError):
        period_bounds("yesterday", BERLIN)
    assert summary_line([], "2026-W46") == "2026-W46: 0 entries, 0 h, 0 projects"


# ------------------------------------------------------------------ MCP

async def test_update_entry_tool_only_own_agent(server, db):
    laptop = create_token(db, "alice", "laptop", ["read", "write"])
    create_token(db, "alice", "phone", ["write"])
    mine = add(db, agent="laptop")
    other = add(db, agent="phone")
    async with Client(server + "/mcp", auth=laptop) as c:
        r = (await c.call_tool("update_entry", {"entry_id": mine.id, "project": "web"})).structured_content
        assert r["project"] == "web"
        with pytest.raises(ToolError):
            await c.call_tool("update_entry", {"entry_id": other.id, "summary": "fremd"})
        with pytest.raises(ToolError):
            await c.call_tool("update_entry", {"entry_id": mine.id})                  # nothing to change
    assert db.get_own_entry(owner="alice", entry_id=other.id).summary == "Startseite gebaut"


async def test_report_prompts(server, db):
    token = create_token(db, "alice", "laptop", ["read"])
    async with Client(server + "/mcp", auth=token) as c:
        names = {p.name for p in await c.list_prompts()}
        assert {"weekly_report", "monthly_report"} <= names
        r = await c.get_prompt("weekly_report", {"weeks_ago": "1", "language": "German"})
        text = r.messages[0].content.text
        start, end, label = period_bounds("last-week", BERLIN)
        assert label in text and f"since='{start:%Y-%m-%d}'" in text and f"until='{end:%Y-%m-%d}'" in text
        assert "German" in text
        r = await c.get_prompt("monthly_report", {"project": "website"})
        assert "project='website'" in r.messages[0].content.text


# ------------------------------------------------------------------ web view

def test_web_edit_delete_rename(proxy, db):
    a = add(db)
    add(db, project="web", summary="Kontakt")
    bobs = add(db, owner="bob", summary="Bobs")
    with httpx.Client(base_url=proxy, headers=as_user("alice", proxy)) as c:
        r = c.post(f"/ui/api/entries/{a.id}", json={"summary": "Startseite neu", "tags": ["deploy"], "duration_min": 30})
        assert r.status_code == 200 and r.json()["summary"] == "Startseite neu" and r.json()["tags"] == ["deploy"]
        assert c.post(f"/ui/api/entries/{a.id}", json={"owner": "bob"}).status_code == 400
        assert c.post(f"/ui/api/entries/{a.id}", json={"summary": ""}).status_code == 400
        assert c.post(f"/ui/api/entries/{bobs.id}", json={"summary": "x"}).status_code == 404   # bob's entry
        assert c.post(f"/ui/api/entries/{bobs.id}/delete", json={}).status_code == 404
        r = c.post("/ui/api/projects/rename", json={"from": "website", "to": "web"})
        assert r.json() == {"from": "website", "to": "web", "entries": 1, "merged": True}
        assert c.post("/ui/api/projects/rename", json={"from": "nope", "to": "x"}).status_code == 404
        assert c.post(f"/ui/api/entries/{a.id}/delete", json={}).json() == {"deleted": a.id}
        assert c.get("/ui/api/entries").json()["total"] == 1
    assert db.get_own_entry(owner="bob", entry_id=bobs.id).summary == "Bobs"
    assert db.list_projects(owner="bob")[0]["project"] == "website"


def test_web_changes_need_proxy_sign_in_and_same_origin(proxy, db):
    a = add(db)
    token = create_token(db, "alice", "reader", ["read"])
    with httpx.Client(base_url=proxy) as c:
        c.post("/ui/login", json={"token": token})
        assert c.post(f"/ui/api/entries/{a.id}", json={"summary": "x"}).status_code == 403   # token sign-in: read only
        assert c.post(f"/ui/api/entries/{a.id}/delete", json={}).status_code == 403
    h = {"X-Worklog-Proxy": SECRET, "Remote-User": "alice"}
    assert httpx.post(proxy + f"/ui/api/entries/{a.id}/delete", json={}, headers=h).status_code == 403   # no Origin
    assert httpx.post(proxy + "/ui/api/projects/rename", json={"from": "website", "to": "x"},
                      headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert db.get_own_entry(owner="alice", entry_id=a.id).summary == "Startseite gebaut"


def test_web_export(server, db):
    add(db, summary="Erster", ts=datetime(2026, 11, 9, 8, tzinfo=timezone.utc))
    add(db, project="other", summary="Zweiter", ts=datetime(2026, 11, 10, 8, tzinfo=timezone.utc))
    add(db, owner="bob", summary="Bobs")
    token = create_token(db, "alice", "reader", ["read"])
    with httpx.Client(base_url=server) as c:
        assert c.get("/ui/api/export").status_code == 401
        c.post("/ui/login", json={"token": token})
        r = c.get("/ui/api/export", params={"format": "md", "since": "2026-11-09", "until": "2026-11-16"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
        assert 'filename="worklog-20261109-20261116.md"' in r.headers["content-disposition"]
        assert r.text.index("Erster") < r.text.index("Zweiter") and "Bobs" not in r.text
        r = c.get("/ui/api/export", params={"format": "csv", "project": "website"})
        assert 'filename="worklog-website-all.csv"' in r.headers["content-disposition"]
        assert "Erster" in r.text and "Zweiter" not in r.text
        assert c.get("/ui/api/export", params={"format": "pdf"}).status_code == 400


# ------------------------------------------------------------------ command line

def test_report_command(tmp_path, capsys):
    from worklog.db import Database
    path = str(tmp_path / "w.db")
    db = Database(path)
    start, _, label = period_bounds("last-week", BERLIN)
    add(db, ts=start.replace(hour=10), duration_min=90)
    add(db, project="garten", ts=start.replace(hour=12), duration_min=30)
    add(db, owner="bob", ts=start.replace(hour=10))
    db.close()
    assert main(["--db", path, "report", "--owner", "alice", "--dir", str(tmp_path / "r")]) == 0
    out = capsys.readouterr().out.strip()
    assert out == f"{label}: 2 entries, 2 h, 2 projects (top: website, garten)"
    text = (tmp_path / "r" / f"worklog-alice-{label}.md").read_text(encoding="utf-8")
    assert text.startswith(f"# Work log {label}") and "garten" in text
    assert main(["--db", path, "report", "--owner", "alice", "--period", "this-month"]) == 0
    assert capsys.readouterr().out.startswith("# Work log")


def test_token_sign_in_with_write_scope_may_edit(server, db):
    """Token sign-in gets the token's rights: write allows corrections and imports, never token management."""
    a = add(db)
    add(db, owner="bob", summary="Bobs")
    writer = create_token(db, "alice", "laptop", ["read", "write"])
    with httpx.Client(base_url=server, headers={"Origin": server}) as c:
        assert c.post("/ui/login", json={"token": writer}).status_code == 200
        s = c.get("/ui/session").json()
        assert (s["can_edit"], s["manage_tokens"]) == (True, False)
        assert c.post(f"/ui/api/entries/{a.id}", json={"summary": "per Token korrigiert"}).status_code == 200
        r = c.post("/ui/api/import", json=[{"ts": "2026-01-01T10:00:00Z", "project": "p", "summary": "importiert"}])
        assert r.json()["created"] == 1
        assert c.get("/ui/api/tokens").status_code == 403
        assert c.post("/ui/api/tokens", json={"name": "x", "scopes": ["write"]}).status_code == 403
        bobs = db.list_entries(owner="bob")[0].id
        assert c.post(f"/ui/api/entries/{bobs}/delete", json={}).status_code == 404       # still only own entries
        db.revoke_token("alice", "laptop")
        assert c.post(f"/ui/api/entries/{a.id}", json={"summary": "x"}).status_code == 401  # follows revocation
    assert db.get_own_entry(owner="alice", entry_id=a.id).summary == "per Token korrigiert"
