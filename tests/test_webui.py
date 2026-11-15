"""Web view: sessions, read endpoints, static files."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from worklog.auth import SESSION_COOKIE, check_session, create_session
from worklog.report import stats
from zoneinfo import ZoneInfo

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")


def seed(db):
    base = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    rows = [
        (0, "website", "claude-code-laptop", "tool", "Deployment der Startseite automatisiert", 30),
        (0, "backup-scripts", "claude-code-laptop", "tool", "Rsync-Job für Fotos eingerichtet", 45),
        (1, "website", "import", "import", "Kontaktformular repariert", 15),
        (3, "website", "claude-code-laptop", "hook", "Bilder komprimiert", None),
    ]
    for day, project, agent, source, summary, minutes in rows:
        db.add_entry(agent=agent, source=source, project=project, summary=summary,
                     duration_min=minutes, ts=base + timedelta(days=day))


# ------------------------------------------------------------------ db + report

def test_query_entries_filters_and_paging(db):
    seed(db)
    entries, total = db.query_entries()
    assert total == 4 and [e.summary for e in entries][0] == "Bilder komprimiert"
    assert db.query_entries(project="website")[1] == 3
    assert db.query_entries(agent="import")[1] == 1
    assert db.query_entries(source="hook")[1] == 1
    assert [e.summary for e in db.query_entries(q="kontakt")[0]] == ["Kontaktformular repariert"]
    assert db.query_entries(q="deploy", project="backup-scripts") == ([], 0)
    assert db.query_entries(q="!!!") == ([], 0)
    page, total = db.query_entries(limit=2, offset=2)
    assert total == 4 and len(page) == 2 and page[0].summary == "Rsync-Job für Fotos eingerichtet"
    since = datetime(2026, 11, 10, tzinfo=UTC)
    assert db.query_entries(since=since, until=since + timedelta(days=1))[1] == 1
    assert len(db.query_entries(limit=None)[0]) == 4


def test_facets(db):
    seed(db)
    f = db.facets()
    assert f["projects"] == [{"name": "backup-scripts", "entries": 1}, {"name": "website", "entries": 3}]
    assert {a["name"]: a["entries"] for a in f["agents"]} == {"claude-code-laptop": 3, "import": 1}
    assert {s["name"] for s in f["sources"]} == {"tool", "import", "hook"}


def test_stats(db):
    seed(db)
    s = stats(db.query_entries(limit=None)[0], BERLIN)
    assert s["totals"] == {"entries": 4, "duration_min": 90, "projects": 2, "days": 3}
    # same minutes: the project with more entries comes first
    assert s["projects"] == [{"project": "website", "entries": 3, "duration_min": 45},
                             {"project": "backup-scripts", "entries": 1, "duration_min": 45}]
    assert [d["date"] for d in s["days"]] == ["2026-11-09", "2026-11-10", "2026-11-12"]
    assert s["days"][0] == {"date": "2026-11-09", "entries": 2, "duration_min": 75}


# ------------------------------------------------------------------ sessions

def test_session_roundtrip_and_revocation(db, tokens):
    session, access = create_session(db, tokens["rw"])
    assert access.client_id == "claude-code-laptop"
    assert tokens["rw"] not in session  # the cookie never carries the token
    got = check_session(db, session)
    assert got.client_id == "claude-code-laptop" and got.scopes == ["read"]
    assert check_session(db, session[:-1] + ("0" if session[-1] != "0" else "1")) is None  # tampered signature
    parts = session.split(".")
    assert check_session(db, ".".join([parts[0], "999", parts[2], parts[3]])) is None       # other token id
    assert check_session(db, "") is None and check_session(db, "v1.a.b.c") is None
    db.revoke_token("claude-code-laptop")
    assert check_session(db, session) is None


def test_session_needs_read_scope_and_valid_token(db, tokens):
    assert create_session(db, "wl_nope") == (None, None)
    session, access = create_session(db, tokens["w"])
    assert session is None and access.client_id == "hook-only"


def test_expired_session(db, tokens, monkeypatch):
    import worklog.auth as auth
    monkeypatch.setattr(auth, "SESSION_TTL", timedelta(seconds=-10))
    session, _ = create_session(db, tokens["r"])
    assert check_session(db, session) is None


# ------------------------------------------------------------------ http

def test_login_flow_and_read_endpoints(server, db, tokens):
    seed(db)
    with httpx.Client(base_url=server) as c:
        assert c.get("/ui/session").status_code == 401
        assert c.get("/api/entries").status_code == 401
        assert c.post("/ui/login", json={"token": "wl_wrong"}).status_code == 401
        assert c.post("/ui/login", json={"token": tokens["w"]}).status_code == 403
        r = c.post("/ui/login", json={"token": tokens["r"]})
        assert r.status_code == 200 and r.json() == {"agent": "reader"}
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
        assert tokens["r"] not in cookie and "Secure" not in cookie  # plain http in tests
        assert c.get("/ui/session").json()["agent"] == "reader"

        data = c.get("/api/entries", params={"project": "website", "limit": 2}).json()
        assert data["total"] == 3 and len(data["entries"]) == 2
        assert data["entries"][0]["ts"].endswith("+01:00")
        assert c.get("/api/entries", params={"q": "rsync"}).json()["total"] == 1
        assert c.get("/api/entries", params={"since": "2026-11-10", "until": "2026-11-11"}).json()["total"] == 1
        assert c.get("/api/entries", params={"since": "gestern"}).status_code == 400
        assert c.get("/api/facets").json()["projects"][1] == {"name": "website", "entries": 3}
        s = c.get("/api/stats", params={"project": "website"}).json()
        assert s["totals"]["entries"] == 3 and s["totals"]["duration_min"] == 45

        # the cookie gives read access only: writing still needs a bearer token
        assert c.post("/api/entries", json={"project": "p", "summary": "x"}).status_code == 401

        assert c.post("/ui/logout").status_code == 200
        assert c.get("/ui/session").status_code == 401


def test_secure_cookie_behind_https_proxy(server, tokens):
    r = httpx.post(server + "/ui/login", json={"token": tokens["r"]}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["set-cookie"]


def test_login_rejects_cross_site_post(server, tokens):
    r = httpx.post(server + "/ui/login", json={"token": tokens["r"]}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    host = server.split("//")[1]
    ok = httpx.post(server + "/ui/login", json={"token": tokens["r"]}, headers={"Origin": f"http://{host}"})
    assert ok.status_code == 200


def test_bearer_token_works_for_read_endpoints(server, db, tokens):
    seed(db)
    h = {"Authorization": f"Bearer {tokens['r']}"}
    assert httpx.get(server + "/api/entries", headers=h).json()["total"] == 4
    assert httpx.get(server + "/api/stats", headers={"Authorization": f"Bearer {tokens['w']}"}).status_code == 403
    assert httpx.get(server + "/api/facets", headers={"Authorization": "Bearer wl_x"}).status_code == 401


@pytest.mark.parametrize("path, content_type, needle", [
    ("/ui/", "text/html", "<title>Worklog</title>"),
    ("/ui/app.js", "text/javascript", "'use strict'"),
    ("/ui/style.css", "text/css", "--series"),
    ("/ui/favicon.svg", "image/svg+xml", "<svg"),
])
def test_static_files_and_headers(server, path, content_type, needle):
    r = httpx.get(server + path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(content_type)
    assert needle in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_redirects(server):
    assert httpx.get(server + "/").headers["location"] == "/ui/"
    assert httpx.get(server + "/ui").headers["location"] == "/ui/"
    assert httpx.get(server + "/ui/unknown.js").status_code == 404


def test_index_has_no_inline_script_or_style():
    from worklog.webui import STATIC
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "<script src=" in html and "<script>" not in html and "style=" not in html and "onclick" not in html
