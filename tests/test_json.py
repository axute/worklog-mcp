"""JSON export and import: no owner in the file, repeatable imports, CLI and web view."""

import dataclasses
import io
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastmcp import Client

from worklog.auth import create_token
from worklog.cli import main
from worklog.db import Database
from worklog.report import read_export, to_json

BERLIN = ZoneInfo("Europe/Berlin")
SECRET = "s" * 40
T = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)


def seed(db, owner="alice"):
    a = db.add_entry(owner=owner, agent="laptop", source="tool", project="website", summary="Startseite gebaut",
                     details="mit npm", tags=["deploy"], duration_min=30, session_id="s1", ts=T)
    db.import_entries(owner, "imp", [{"external_id": "claude-code:abc:0", "ts": "2026-10-01T10:00:00Z",
                                      "project": "alt", "summary": "Alter Import"}])
    return a


def export(db, owner="alice"):
    entries, _ = db.query_entries(owner=owner, limit=None)
    return json.loads(to_json(list(reversed(entries)), BERLIN, db.instance_id()))


def test_export_format(db):
    a = seed(db)
    data = export(db)
    assert data["format"] == "worklog-export" and data["version"] == 1 and data["timezone"] == "Europe/Berlin"
    first, second = data["entries"]
    assert "owner" not in json.dumps(data) and "alice" not in json.dumps(data)
    assert first["external_id"] == "claude-code:abc:0"                                   # kept
    assert second["external_id"] == f"worklog:{db.instance_id()}:{a.id}"                  # derived
    assert {k: second[k] for k in ("agent", "source", "project", "tags", "duration_min", "session_id")} == {
        "agent": "laptop", "source": "tool", "project": "website", "tags": ["deploy"], "duration_min": 30,
        "session_id": "s1"}
    assert second["ts"] == "2026-11-10T10:00:00+01:00"


def test_import_into_other_user_and_repeat(db):
    seed(db)
    data = export(db)
    data["entries"][0]["owner"] = "mallory"                                               # ignored
    r = db.import_export("bob", read_export(data))
    assert r == {"created": 2, "updated": 0, "skipped": 0, "errors": []}
    bob = {e.summary: e for e in db.list_entries(owner="bob")}
    assert bob["Startseite gebaut"].agent == "laptop" and bob["Startseite gebaut"].source == "tool"
    assert bob["Startseite gebaut"].ts == T and bob["Startseite gebaut"].owner == "bob"
    assert db.import_export("bob", read_export(data))["skipped"] == 2                     # repeatable
    data["entries"][1]["summary"] = "Startseite gebaut und deployt"
    assert db.import_export("bob", read_export(data), update=True)["updated"] == 2
    assert len(db.list_entries(owner="alice")) == 2                                       # alice untouched


def test_import_back_into_same_instance(db):
    a = seed(db)
    data = export(db)
    assert db.import_export("alice", read_export(data))["skipped"] == 2                   # originals still there
    db.delete_entry(owner="alice", entry_id=a.id)
    r = db.import_export("alice", read_export(data))
    assert (r["created"], r["skipped"]) == (1, 1)                                        # deleted one comes back
    assert db.search_entries("startseite", owner="alice")[0].details == "mit npm"


def test_import_into_other_instance(db, tmp_path):
    seed(db)
    other = Database(tmp_path / "other.db")
    data = export(db)
    assert other.import_export("alice", read_export(data))["created"] == 2
    assert other.import_export("alice", read_export(data))["skipped"] == 2
    other.close()


def test_import_validation(db):
    items = [{"ts": "2026-01-01T10:00:00Z", "project": "p", "summary": "ohne id"},
             {"ts": "2026-01-01T10:00:00Z", "project": "", "summary": "x"},
             {"project": "p", "summary": "kein ts"},
             {"ts": "2026-01-01", "project": "p", "summary": "x", "source": "magic"},
             {"ts": "2026-01-01", "project": "p", "summary": "x", "agent": "bad agent"},
             "kein Objekt"]
    r = db.import_export("alice", items)
    assert r["created"] == 1 and [e["index"] for e in r["errors"]] == [1, 2, 3, 4, 5]
    assert db.import_export("alice", items[:1])["skipped"] == 1                           # derived id is stable
    with pytest.raises(ValueError):
        read_export({"format": "something-else", "entries": []})
    with pytest.raises(ValueError):
        read_export({"format": "worklog-export", "version": 99, "entries": []})
    with pytest.raises(ValueError):
        read_export({"entries": "x"})


def test_cli_export_import(tmp_path, capsys, monkeypatch):
    src, dst = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    db = Database(src)
    seed(db)
    db.close()
    out = tmp_path / "export.json"
    assert main(["--db", src, "export", "--owner", "alice", "--format", "json", "-o", str(out)]) == 0
    assert json.loads(out.read_text())["entries"][0]["project"] == "alt"
    assert main(["--db", dst, "import", "--owner", "axute", str(out)]) == 0
    assert "created 2," in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(out.read_text()))
    assert main(["--db", dst, "import", "--owner", "axute", "-"]) == 0
    assert "skipped 2" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert main(["--db", dst, "import", "--owner", "axute", str(bad)]) == 1


@pytest.fixture
def proxy(make_server, settings):
    return make_server(dataclasses.replace(settings, proxy_secret=SECRET))


def test_web_export_import(proxy, db):
    seed(db)
    h = {"X-Worklog-Proxy": SECRET, "Remote-User": "alice", "Origin": proxy}
    with httpx.Client(base_url=proxy, headers=h) as c:
        r = c.get("/ui/api/export", params={"format": "json", "project": "website"})
        assert r.headers["content-type"].startswith("application/json")
        assert 'filename="worklog-website-all.json"' in r.headers["content-disposition"]
        data = r.json()
        assert len(data["entries"]) == 1 and data["filters"] == {"project": "website"}
    hb = {"X-Worklog-Proxy": SECRET, "Remote-User": "bob", "Origin": proxy, "Content-Type": "application/json"}
    with httpx.Client(base_url=proxy, headers=hb) as c:
        assert c.post("/ui/api/import", content=json.dumps(data)).json()["created"] == 1
        assert c.post("/ui/api/import", content=json.dumps(data)).json()["skipped"] == 1
        assert c.post("/ui/api/import", content="{nope").status_code == 400
        assert c.post("/ui/api/import", content=json.dumps({"format": "x", "entries": []})).status_code == 400
    assert [e.summary for e in db.list_entries(owner="bob")] == ["Startseite gebaut"]
    token = create_token(db, "alice", "reader", ["read"])
    with httpx.Client(base_url=proxy) as c:
        c.post("/ui/login", json={"token": token})
        assert c.get("/ui/api/export", params={"format": "json"}).status_code == 200
        assert c.post("/ui/api/import", json=data).status_code == 403                     # read only


async def test_backfill_my_commits_prompt(server, db):
    token = create_token(db, "alice", "laptop", ["read"])
    async with Client(server + "/mcp", auth=token) as c:
        text = (await c.get_prompt("backfill_my_commits", {"since": "2025-01-01"})).messages[0].content.text
        assert "THIS project" in text and "git shortlog -sne --all" in text and "--author" in text
        assert "Wait until I confirm" in text and "from 2025-01-01 on" in text and "log_entries" in text
