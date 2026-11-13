"""Search, summaries, projects, REST endpoints, export and backup."""

import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from worklog.cli import main
from worklog.db import MIGRATIONS, Database, fts_query

UTC = timezone.utc


def seed(db):
    base = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)  # Monday 09:00 Berlin
    rows = [
        (0, "website", "Deployment der Startseite automatisiert", "GitHub Actions Workflow", ["ci"], 30),
        (0, "backup-scripts", "Rsync-Job für Fotos eingerichtet", None, ["backup"], 45),
        (1, "website", "Kontaktformular repariert", "Spam-Filter angepasst", [], 15),
        (3, "website", "Bilder komprimiert", None, ["performance"], None),
    ]
    for day, project, summary, details, tags, minutes in rows:
        db.add_entry(agent="claude-code-laptop", source="tool", project=project, summary=summary,
                     details=details, tags=tags, duration_min=minutes, ts=base + timedelta(days=day))


# ------------------------------------------------------------------ db level

def test_fts_query_is_safe():
    assert fts_query('deploy "AND" OR -x*') == '"deploy"* "AND"* "OR"* "x"*'
    assert fts_query("  ") == ""


def test_search(db):
    seed(db)
    assert [e.summary for e in db.search_entries("deploy")] == ["Deployment der Startseite automatisiert"]
    assert [e.summary for e in db.search_entries("spam")] == ["Kontaktformular repariert"]  # found in details
    assert [e.summary for e in db.search_entries("fotos rsync")] == ["Rsync-Job für Fotos eingerichtet"]
    assert db.search_entries("website ci")[0].summary.startswith("Deployment")  # project + tag
    assert db.search_entries("rsync", project="website") == []
    assert db.search_entries("fur")[0].project == "backup-scripts"  # diacritics removed: für ~ fur
    with pytest.raises(ValueError):
        db.search_entries("  !!! ")


def test_search_follows_updates_and_imports(db):
    db.import_entries("imp", [{"external_id": "x:1", "ts": "2026-01-01T10:00:00Z", "project": "p", "summary": "alter Text"}])
    assert len(db.search_entries("alter")) == 1
    db.import_entries("imp", [{"external_id": "x:1", "ts": "2026-01-01T10:00:00Z", "project": "p", "summary": "neuer Text"}], update=True)
    assert db.search_entries("alter") == []
    assert len(db.search_entries("neuer")) == 1


def test_upgrade_from_v1_indexes_old_entries(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.execute("PRAGMA user_version = 1")
    conn.execute("INSERT INTO entries (ts, created_at, agent, source, project, summary) VALUES "
                 "('2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'a', 'tool', 'p', 'Altbestand gefunden')")
    conn.commit()
    conn.close()
    d = Database(path)
    assert d.schema_version() == len(MIGRATIONS)
    assert [e.summary for e in d.search_entries("altbestand")] == ["Altbestand gefunden"]
    d.close()


def test_projects(db):
    seed(db)
    p = db.list_projects()
    assert [x["project"] for x in p] == ["website", "backup-scripts"]
    assert p[0]["entries"] == 3 and p[0]["duration_min"] == 45


def test_import_idempotent(db):
    items = [
        {"external_id": "claude-code:abc", "ts": "2026-10-01T09:15:00+02:00", "project": "p", "summary": "Sitzung A", "tags": ["Import"]},
        {"external_id": "claude-code:def", "ts": "2026-10-02", "project": "p", "summary": "Sitzung B", "duration_min": 10},
    ]
    assert db.import_entries("import", items) == {"created": 2, "updated": 0, "skipped": 0, "errors": []}
    assert db.import_entries("import", items) == {"created": 0, "updated": 0, "skipped": 2, "errors": []}
    entries = db.list_entries()
    assert len(entries) == 2
    a = next(e for e in entries if e.external_id == "claude-code:abc")
    assert a.ts == datetime(2026, 10, 1, 7, 15, tzinfo=UTC)  # original time kept
    assert a.source == "import" and a.agent == "import" and a.tags == ["import"]


def test_import_reports_bad_items_and_keeps_good_ones(db):
    res = db.import_entries("imp", [
        {"external_id": "ok:1", "ts": "2026-10-01", "project": "p", "summary": "gut"},
        {"ts": "2026-10-01", "project": "p", "summary": "ohne id"},
        {"external_id": "bad:2", "ts": "kein datum", "project": "p", "summary": "x"},
        {"external_id": "bad:3", "ts": "2026-10-01", "project": "p", "summary": "x" * 301},
        {"external_id": "bad:4", "ts": "2026-10-01", "project": "p", "summary": "x", "duration_min": "5"},
        "kein objekt",
    ])
    assert res["created"] == 1
    assert [e["index"] for e in res["errors"]] == [1, 2, 3, 4, 5]


# ------------------------------------------------------------------ MCP tools

async def test_search_and_projects_tools(server, db, tokens):
    seed(db)
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        hits = (await c.call_tool("search_entries", {"query": "kontakt"})).data
        assert [h["summary"] for h in hits] == ["Kontaktformular repariert"]
        projects = (await c.call_tool("list_projects", {})).data
        assert projects[0]["project"] == "website"
        assert projects[0]["last_ts"].endswith("+01:00")
        with pytest.raises(ToolError):
            await c.call_tool("search_entries", {"query": "!!!"})
    async with Client(server + "/mcp", auth=tokens["w"]) as c:
        with pytest.raises(ToolError, match="read"):
            await c.call_tool("search_entries", {"query": "x"})


async def test_summarize_period(server, db, tokens):
    seed(db)
    async with Client(server + "/mcp", auth=tokens["rw"]) as c:
        s = (await c.call_tool("summarize_period", {"since": "2026-11-09", "until": "2026-11-16"})).data
        assert [d["date"] for d in s["days"]] == ["2026-11-09", "2026-11-10", "2026-11-12"]
        monday = s["days"][0]
        assert [p["project"] for p in monday["projects"]] == ["website", "backup-scripts"]
        assert monday["duration_min"] == 75
        assert monday["projects"][0]["entries"][0]["time"] == "09:00"
        assert s["totals"] == {"entries": 4, "duration_min": 90, "projects": {
            "website": {"entries": 3, "duration_min": 45}, "backup-scripts": {"entries": 1, "duration_min": 45}}}
        only = (await c.call_tool("summarize_period", {"since": "2026-11-09", "until": "2026-11-16", "project": "backup-scripts"})).data
        assert only["totals"]["entries"] == 1
        with pytest.raises(ToolError, match="after"):
            await c.call_tool("summarize_period", {"since": "2026-11-16", "until": "2026-11-09"})


# ------------------------------------------------------------------ REST

def test_rest_single_entry(server, db, tokens):
    r = httpx.post(server + "/api/entries", json={"project": "p", "summary": "per Hook"},
                   headers={"Authorization": f"Bearer {tokens['w']}"})
    assert r.status_code == 201
    e = db.get_entry(r.json()["id"])
    assert e.source == "hook" and e.agent == "hook-only"


@pytest.mark.parametrize("auth, status", [(None, 401), ("Bearer wl_x", 401), ("Token abc", 401), ("reader", 403)])
def test_rest_auth(server, tokens, auth, status):
    headers = {}
    if auth == "reader":
        headers["Authorization"] = f"Bearer {tokens['r']}"
    elif auth:
        headers["Authorization"] = auth
    r = httpx.post(server + "/api/entries", json={"project": "p", "summary": "x"}, headers=headers)
    assert r.status_code == status
    if status == 401:
        assert r.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("body, status, msg", [
    ({"project": "p"}, 400, "summary"),
    ({"project": "p", "summary": "x", "agent": "fremd"}, 400, "unknown fields: agent"),
    ({"project": "p", "summary": "x", "tags": "a,b"}, 400, "tags"),
    ([1, 2], 400, "object"),
])
def test_rest_validation(server, tokens, body, status, msg):
    r = httpx.post(server + "/api/entries", json=body, headers={"Authorization": f"Bearer {tokens['w']}"})
    assert r.status_code == status
    assert msg in r.json()["error"]


def test_rest_wrong_content_type(server, tokens):
    r = httpx.post(server + "/api/entries", content="project=p", headers={
        "Authorization": f"Bearer {tokens['w']}", "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_rest_bulk_twice_no_duplicates(server, db, tokens):
    items = [{"external_id": f"claude-code:{i}", "ts": f"2026-10-0{i + 1}T10:00:00Z", "project": "p", "summary": f"S{i}"} for i in range(3)]
    h = {"Authorization": f"Bearer {tokens['w']}"}
    r1 = httpx.post(server + "/api/entries/bulk", json={"entries": items}, headers=h).json()
    r2 = httpx.post(server + "/api/entries/bulk", json=items, headers=h).json()
    assert (r1["created"], r2["created"], r2["skipped"]) == (3, 0, 3)
    items[0]["summary"] = "S0 korrigiert"
    r3 = httpx.post(server + "/api/entries/bulk?update=true", json=items, headers=h).json()
    assert (r3["updated"], r3["created"]) == (3, 0)
    assert len(db.list_entries(limit=50)) == 3
    assert db.list_entries(limit=50)[-1].summary == "S0 korrigiert"


def test_rest_bulk_limit(server, tokens):
    items = [{"external_id": f"x:{i}", "ts": "2026-10-01", "project": "p", "summary": "s"} for i in range(501)]
    r = httpx.post(server + "/api/entries/bulk", json=items, headers={"Authorization": f"Bearer {tokens['w']}"})
    assert r.status_code == 413


def test_healthz_needs_no_token_but_api_does(server):
    assert httpx.get(server + "/healthz").text == "ok"
    assert httpx.get(server + "/api/entries").status_code == 405


# ------------------------------------------------------------------ CLI export / backup

def test_export_markdown_and_csv(tmp_path, capsys):
    path = str(tmp_path / "w.db")
    d = Database(path)
    seed(d)
    d.close()
    assert main(["--db", path, "export", "--since", "2026-11-09", "--until", "2026-11-11"]) == 0
    md = capsys.readouterr().out
    assert "## 2026-11-09" in md and "### website (30 min)" in md
    assert "- 09:00 · claude-code-laptop: Deployment der Startseite automatisiert – 30 min `ci`" in md
    assert "Bilder komprimiert" not in md  # outside the range
    assert "**Total:** 3 entries, 90 min" in md
    out = tmp_path / "x.csv"
    assert main(["--db", path, "export", "--format", "csv", "--project", "website", "-o", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("id,ts,agent")
    assert len(lines) == 4


def test_backup_keeps_newest(tmp_path, capsys, monkeypatch):
    path = str(tmp_path / "w.db")
    d = Database(path)
    seed(d)
    d.close()
    backups = tmp_path / "backups"
    backups.mkdir()
    for i in range(3):
        (backups / f"worklog-2026010{i}-000000.db").write_text("old")
    assert main(["--db", path, "backup", "--keep", "2"]) == 0
    files = sorted(p.name for p in backups.iterdir())
    assert len(files) == 2 and files[0] == "worklog-20260102-000000.db"
    copy = Database(backups / files[-1])
    assert len(copy.list_entries()) == 4
    copy.close()
