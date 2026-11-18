"""Likely duplicates: detection, merging, "not a duplicate", and the hint in log_entry."""

import dataclasses
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastmcp import Client

from worklog.auth import create_token
from worklog.dupes import find_groups, similarity

T0 = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
SECRET = "s" * 40


def add(db, summary, minutes_later=0, owner="alice", project="website", agent="laptop", **kw):
    return db.add_entry(owner=owner, agent=agent, source="tool", project=project, summary=summary,
                        ts=T0 + timedelta(minutes=minutes_later), **kw)


@pytest.mark.parametrize("a, b, same", [
    ("Web-Ansicht live geschaltet", "Web-Ansicht ist live geschaltet", True),
    ("Backup-Script eingerichtet", "Backup Script für Fotos eingerichtet", True),
    ("Traefik Router angepasst", "Authelia Middleware ergänzt", False),
    ("Import Etappe 3 fertig: +139 Einträge", "Import Etappe 4 fertig: +116 Einträge", False),   # numbers differ
    ("", "x", False),
])
def test_similarity(a, b, same):
    assert (similarity(a, b) >= 0.75) is same


def test_find_groups(db):
    a = add(db, "Startseite deployt")
    b = add(db, "Startseite deployed", 30, agent="hook")             # other agent, same work
    c = add(db, "Die Startseite deployt", 90)                         # joins the group
    add(db, "Startseite deployt", 30, project="other")               # other project
    add(db, "Startseite deployt", 60 * 5)                             # outside the time window
    add(db, "Kontaktformular repariert", 10)
    entries, _ = db.query_entries(owner="alice", limit=None)
    groups = find_groups(entries)
    assert [[e.id for e in g["entries"]] for g in groups] == [[a.id, b.id, c.id]]
    assert find_groups(entries, ignored={(a.id, b.id), (a.id, c.id), (b.id, c.id)}) == []


def test_merge_entries(db):
    a = add(db, "Startseite deployt", details="Build mit npm", tags=["deploy"], duration_min=20)
    b = add(db, "Startseite deployed", -5, details="Cache geleert", tags=["deploy", "cdn"], duration_min=30,
            agent="hook")
    other = add(db, "Bobs Eintrag", owner="bob")
    with pytest.raises(KeyError):
        db.merge_entries(owner="alice", keep_id=a.id, remove_ids=[other.id])   # bob's entry
    with pytest.raises(ValueError):
        db.merge_entries(owner="alice", keep_id=a.id, remove_ids=[a.id])
    m = db.merge_entries(owner="alice", keep_id=a.id, remove_ids=[b.id])
    assert (m.id, m.summary, m.ts, m.duration_min, m.tags) == (a.id, "Startseite deployt", b.ts, 30, ["deploy", "cdn"])
    assert m.details == "Build mit npm\n\nCache geleert"
    assert [e.id for e in db.list_entries(owner="alice")] == [a.id]
    assert db.search_entries("cache", owner="alice")[0].id == a.id
    assert db.get_own_entry(owner="bob", entry_id=other.id)


def test_merge_keeps_an_import_id(db):
    db.import_entries("alice", "imp", [{"external_id": "x:1", "ts": T0.isoformat(), "project": "website",
                                        "summary": "Startseite deployt"}])
    imported = db.list_entries(owner="alice")[0]
    tool = add(db, "Startseite deployed", 10)
    m = db.merge_entries(owner="alice", keep_id=tool.id, remove_ids=[imported.id])
    assert m.external_id == "x:1"            # a later re-import does not bring the removed entry back
    assert db.import_entries("alice", "imp", [{"external_id": "x:1", "ts": T0.isoformat(), "project": "website",
                                               "summary": "Startseite deployt"}])["skipped"] == 1


async def test_log_entry_reports_likely_duplicate(server, db):
    token = create_token(db, "alice", "laptop", ["read", "write"])
    async with Client(server + "/mcp", auth=token) as c:
        first = (await c.call_tool("log_entry", {"project": "website", "summary": "Startseite deployt"})).structured_content
        assert "possible_duplicate_of" not in first
        second = (await c.call_tool("log_entry", {"project": "website", "summary": "Startseite deployed"})).structured_content
        assert second["possible_duplicate_of"] == [first["id"]] and "update_entry" in second["hint"]
        third = (await c.call_tool("log_entry", {"project": "website", "summary": "Impressum ergänzt"})).structured_content
        assert "possible_duplicate_of" not in third


@pytest.fixture
def proxy(make_server, settings):
    return make_server(dataclasses.replace(settings, proxy_secret=SECRET))


def test_web_duplicates(proxy, db):
    a = add(db, "Startseite deployt", duration_min=10)
    b = add(db, "Startseite deployed", 20, duration_min=15)
    c = add(db, "Impressum ergänzt", 30)
    d = add(db, "Impressum ergaenzt", 40)
    add(db, "Startseite deployt", owner="bob")
    add(db, "Startseite deployed", 5, owner="bob")
    h = {"X-Worklog-Proxy": SECRET, "Remote-User": "alice", "Origin": proxy}
    with httpx.Client(base_url=proxy, headers=h) as cl:
        groups = cl.get("/ui/api/duplicates").json()["groups"]
        assert sorted(sorted(e["id"] for e in g["entries"]) for g in groups) == [[a.id, b.id], [c.id, d.id]]
        assert cl.post("/ui/api/duplicates/ignore", json={"ids": [c.id, d.id]}).json() == {"ignored": [c.id, d.id]}
        r = cl.post("/ui/api/duplicates/merge", json={"keep": b.id, "remove": [a.id]})
        assert r.status_code == 200 and r.json()["duration_min"] == 15
        assert cl.get("/ui/api/duplicates").json()["groups"] == []
        assert cl.post("/ui/api/duplicates/merge", json={"keep": b.id, "remove": "x"}).status_code == 400
        bob_ids = [e.id for e in db.list_entries(owner="bob")]
        assert cl.post("/ui/api/duplicates/merge", json={"keep": b.id, "remove": bob_ids}).status_code == 404
        assert cl.post("/ui/api/duplicates/ignore", json={"ids": bob_ids}).status_code == 404
    bob = {"X-Worklog-Proxy": SECRET, "Remote-User": "bob"}
    assert len(httpx.get(proxy + "/ui/api/duplicates", headers=bob).json()["groups"]) == 1   # bob's own group only
    token = create_token(db, "alice", "reader", ["read"])
    with httpx.Client(base_url=proxy) as cl:
        cl.post("/ui/login", json={"token": token})
        assert cl.get("/ui/api/duplicates").status_code == 200
        assert cl.post("/ui/api/duplicates/ignore", json={"ids": [a.id, b.id]}).status_code == 403   # read only
