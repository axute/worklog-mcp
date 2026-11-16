"""Strict separation between users: no way of reading or changing another user's data."""

import dataclasses
import sqlite3

import httpx
import pytest
from fastmcp import Client

from worklog.auth import check_session, create_session, create_token
from worklog.cli import main
from worklog.db import MIGRATIONS, Database

SECRET = "s" * 40


@pytest.fixture
def two(db):
    """alice and bob, each with a read+write token named the same ('laptop')."""
    return {
        "alice": create_token(db, "alice", "laptop", ["read", "write"]),
        "bob": create_token(db, "bob", "laptop", ["read", "write"]),
    }


@pytest.fixture
def proxy(make_server, settings):
    return make_server(dataclasses.replace(settings, proxy_secret=SECRET))


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def as_user(user, server=None, **extra):
    h = {"X-Worklog-Proxy": SECRET, "Remote-User": user, **extra}
    if server:
        h["Origin"] = server
    return h


def test_same_token_name_per_user_and_owner_in_token(db, two):
    assert db.get_token("alice", "laptop").owner == "alice"
    assert db.get_token("bob", "laptop").owner == "bob"
    with pytest.raises(ValueError):
        create_token(db, "alice", "laptop", ["read"])           # unique per owner
    assert [t.owner for t in db.list_tokens(owner="bob")] == ["bob"]
    assert len(db.list_tokens(owner=None)) == 2                 # command line only


def test_db_queries_never_cross_owners(db):
    db.add_entry(owner="alice", agent="a", source="tool", project="geheim", summary="Alices Steuererklärung")
    db.add_entry(owner="bob", agent="b", source="tool", project="garten", summary="Bobs Hochbeet")
    item = {"external_id": "x:1", "ts": "2026-01-01T10:00:00Z", "project": "p", "summary": "import"}
    assert db.import_entries("alice", "imp", [item])["created"] == 1
    assert db.import_entries("bob", "imp", [item])["created"] == 1           # same external id, other user
    assert db.import_entries("bob", "imp", [{**item, "summary": "neu"}], update=True)["updated"] == 1
    assert {e.summary for e in db.list_entries(owner="alice")} == {"Alices Steuererklärung", "import"}

    for owner, other in (("alice", "Bobs"), ("bob", "Alices")):
        assert all(other not in e.summary for e in db.list_entries(owner=owner, limit=500))
        assert db.search_entries(other.rstrip("s"), owner=owner) == []
        entries, total = db.query_entries(owner=owner, limit=None)
        assert total == 2 and all(e.owner == owner for e in entries)
        assert db.query_entries(owner=owner, q=other.rstrip("s")) == ([], 0)
        assert {p["project"] for p in db.list_projects(owner=owner)} == ({"geheim", "p"} if owner == "alice" else {"garten", "p"})
        assert {f["name"] for f in db.facets(owner=owner)["projects"]} == {p["project"] for p in db.list_projects(owner=owner)}
    with pytest.raises(ValueError):
        db.list_entries(owner="")
    with pytest.raises(TypeError):
        db.list_entries()  # the owner is required


async def test_mcp_tools_are_per_user(server, db, two):
    async with Client(server + "/mcp", auth=two["alice"]) as c:
        await c.call_tool("log_entry", {"project": "geheim", "summary": "Alices Notiz"})
    async with Client(server + "/mcp", auth=two["bob"]) as c:
        await c.call_tool("log_entry", {"project": "garten", "summary": "Bobs Notiz"})
        listed = (await c.call_tool("list_entries", {})).structured_content["result"]
        assert [e["summary"] for e in listed] == ["Bobs Notiz"]
        assert (await c.call_tool("search_entries", {"query": "Alices"})).structured_content["result"] == []
        projects = (await c.call_tool("list_projects", {})).structured_content["result"]
        assert [p["project"] for p in projects] == ["garten"]
        summary = (await c.call_tool("summarize_period", {"since": "2000-01-01", "until": "2100-01-01"})).structured_content
        assert "Alices" not in str(summary) and "Bobs Notiz" in str(summary)
    assert [e.owner for e in db.list_entries(owner="alice")] == ["alice"]


def test_rest_api_is_per_user(server, db, two):
    assert httpx.post(server + "/api/entries", json={"project": "geheim", "summary": "Alice per Hook"},
                      headers=bearer(two["alice"])).status_code == 201
    bulk = {"entries": [{"external_id": "e:1", "ts": "2026-01-01T10:00:00Z", "project": "p", "summary": "Bob Import"}]}
    assert httpx.post(server + "/api/entries/bulk", json=bulk, headers=bearer(two["bob"])).json()["created"] == 1
    bob = httpx.get(server + "/api/entries", headers=bearer(two["bob"])).json()
    assert [e["summary"] for e in bob["entries"]] == ["Bob Import"]
    assert httpx.get(server + "/api/facets", headers=bearer(two["bob"])).json()["projects"] == [{"name": "p", "entries": 1}]
    assert httpx.get(server + "/api/stats", headers=bearer(two["alice"])).json()["totals"]["entries"] == 1


def test_web_view_is_per_user(proxy, db, two):
    db.add_entry(owner="alice", agent="laptop", source="tool", project="geheim", summary="Alices Notiz")
    db.add_entry(owner="bob", agent="laptop", source="tool", project="garten", summary="Bobs Notiz")
    with httpx.Client(base_url=proxy, headers=as_user("bob", proxy)) as c:
        assert [e["summary"] for e in c.get("/ui/api/entries").json()["entries"]] == ["Bobs Notiz"]
        assert c.get("/ui/api/entries", params={"q": "Alices"}).json()["total"] == 0
        assert c.get("/ui/api/entries", params={"project": "geheim"}).json()["total"] == 0
        assert c.get("/ui/api/facets").json()["projects"] == [{"name": "garten", "entries": 1}]
        assert c.get("/ui/api/stats").json()["totals"]["entries"] == 1
        # tokens: only bob's own; alice's token of the same name is out of reach
        assert [t["name"] for t in c.get("/ui/api/tokens").json()["tokens"]] == ["laptop"]
        assert c.post("/ui/api/tokens/laptop/revoke", json={}).status_code == 200
    assert db.get_token("bob", "laptop").revoked_at is not None
    assert db.get_token("alice", "laptop").revoked_at is None                 # untouched
    with httpx.Client(base_url=proxy, headers=as_user("bob", proxy)) as c:
        assert c.post("/ui/api/tokens/nobody/revoke", json={}).status_code == 404
        new = c.post("/ui/api/tokens", json={"name": "phone", "scopes": ["write"]}).json()
    assert db.get_token("bob", "phone").owner == "bob"
    with pytest.raises(KeyError):
        db.get_token("alice", "phone")
    # a new user simply starts empty
    assert httpx.get(proxy + "/ui/api/entries", headers=as_user("carol")).json()["total"] == 0
    assert new["token"].startswith("wl_")


def test_token_session_is_per_user(server, db, two):
    db.add_entry(owner="alice", agent="laptop", source="tool", project="geheim", summary="Alices Notiz")
    with httpx.Client(base_url=server) as c:
        assert c.post("/ui/login", json={"token": two["bob"]}).json() == {"user": "bob", "agent": "laptop"}
        assert c.get("/ui/api/entries").json()["total"] == 0
    session, access = create_session(db, two["alice"])
    assert check_session(db, session).claims == {"owner": "alice"}
    db.revoke_token("alice", "laptop")
    assert check_session(db, session) is None


def test_migration_assigns_existing_data_and_tokens_keep_working(tmp_path):
    path = tmp_path / "v3.db"
    # a version 3 database, as it exists before this change
    conn = sqlite3.connect(path)
    for sql in MIGRATIONS[:3]:
        conn.executescript(sql)
    conn.execute("PRAGMA user_version = 3")
    from worklog.auth import generate_token, hash_token
    token = generate_token()
    conn.execute("INSERT INTO tokens (name, token_hash, scopes, created_at) VALUES ('laptop', ?, 'read,write', "
                 "'2026-01-01T00:00:00Z')", (hash_token(token),))
    conn.execute("INSERT INTO entries (ts, created_at, agent, source, project, summary, external_id) VALUES "
                 "('2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'laptop', 'import', 'p', 'Altbestand', 'x:1')")
    conn.commit()
    conn.close()
    d = Database(path, legacy_owner="axute")
    from worklog.auth import check_token
    access = check_token(d, token)
    assert access.client_id == "laptop" and access.claims == {"owner": "axute"}
    assert [e.summary for e in d.search_entries("altbestand", owner="axute")] == ["Altbestand"]
    assert d.import_entries("axute", "laptop", [{"external_id": "x:1", "ts": "2026-01-01T00:00:00Z",
                                                "project": "p", "summary": "dup"}])["skipped"] == 1
    d.add_entry(owner="axute", agent="laptop", source="tool", project="p", summary="neu nach Migration")
    assert len(d.search_entries("neu", owner="axute")) == 1                     # triggers keep FTS in sync
    d.close()
    assert Database(path).schema_version() == len(MIGRATIONS)                  # later opens need no owner


def test_empty_database_needs_no_legacy_owner(tmp_path):
    assert Database(tmp_path / "new.db").schema_version() == len(MIGRATIONS)


def test_cli_owner(tmp_path, capsys):
    path = str(tmp_path / "w.db")
    assert main(["--db", path, "token", "create", "laptop", "--owner", "alice"]) == 0
    assert main(["--db", path, "token", "create", "laptop", "--owner", "bob"]) == 0
    capsys.readouterr()
    assert main(["--db", path, "token", "list", "--owner", "bob"]) == 0
    out = capsys.readouterr().out
    assert "bob" in out and "alice" not in out
    assert main(["--db", path, "token", "revoke", "laptop", "--owner", "bob"]) == 0
    with pytest.raises(SystemExit):
        main(["--db", path, "token", "create", "x"])                          # --owner is required
