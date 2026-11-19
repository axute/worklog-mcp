"""Notifications set up per user in the web view: allowed services, targets, schedule, scheduler."""

import dataclasses
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import httpx
import pytest

from worklog import notify as nt
from worklog.auth import create_token

BERLIN = ZoneInfo("Europe/Berlin")
SECRET = "s" * 40


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


@pytest.fixture
def allow_json(settings):
    return dataclasses.replace(settings, notify_allow=frozenset({"json"}))


def test_allowed_services(settings):
    assert "json" not in nt.allowed_schemes(settings) and "ntfy" in nt.allowed_schemes(settings)
    s2 = dataclasses.replace(settings, notify_allow=frozenset({"json"}), notify_deny=frozenset({"ntfy", "ntfys"}))
    assert "json" in nt.allowed_schemes(s2) and "ntfy" not in nt.allowed_schemes(s2)
    names = {x["name"]: x for x in nt.services(settings)}
    assert names["ntfy"]["schemes"] == ["ntfy", "ntfys"] and names["ntfy"]["docs"].startswith("https://")
    assert "JSON" not in names


def test_check_target_and_mask(settings):
    assert nt.check_target(" ntfy://my-topic ", settings) == "ntfy://my-topic"
    for bad in ("json://example.com/hook", "ntfy:/x", "nonsense", "tgram://", "x" * 3000):
        with pytest.raises(ValueError):
            nt.check_target(bad, settings)
    assert nt.mask("tgram://123456:ABCDEFsecret/987654") == "tgram://…7654"
    assert nt.mask("ntfy://abc") == "ntfy://…"


@pytest.mark.parametrize("now, schedule, due", [
    ("2026-11-16T07:29", {"weekly": True, "weekday": 0, "monthly": False, "time": "07:30"}, []),
    ("2026-11-16T07:30", {"weekly": True, "weekday": 0, "monthly": False, "time": "07:30"}, ["last-week"]),
    ("2026-11-19T12:00", {"weekly": True, "weekday": 0, "monthly": False, "time": "07:30"}, ["last-week"]),   # catch up
    ("2026-11-19T12:00", {"weekly": True, "weekday": 4, "monthly": False, "time": "07:30"}, []),              # Friday
    ("2026-11-01T08:00", {"weekly": False, "weekday": 0, "monthly": True, "time": "07:30"}, ["last-month"]),
    ("2026-11-19T08:00", {"weekly": False, "weekday": 0, "monthly": False, "time": "07:30"}, []),
])
def test_due_reports(now, schedule, due):
    assert nt.due_reports(schedule, datetime.fromisoformat(now).replace(tzinfo=BERLIN)) == due


def test_scheduler_sends_once(db, allow_json, catcher):
    now = datetime(2026, 11, 16, 8, tzinfo=BERLIN)
    db.add_entry(owner="alice", agent="a", source="tool", project="website", summary="x", duration_min=60,
                 ts=datetime(2026, 11, 12, 10, tzinfo=BERLIN))
    db.add_notify_target(owner="alice", url=catcher, label="Test")
    db.set_report_schedule(owner="alice", weekly=True, weekday=0, monthly=False, time="07:30")
    db.set_report_schedule(owner="bob", weekly=True, weekday=0, monthly=False, time="07:30")   # no targets
    done = nt.run_due_reports(db, allow_json, now)
    assert sorted(done) == [("alice", "2026-W46", "sent 1, failed 0"), ("bob", "2026-W46", "no targets")]
    assert Catcher.received[0]["title"] == "Worklog 2026-W46" and "**website**" in Catcher.received[0]["message"]
    assert nt.run_due_reports(db, allow_json, now + timedelta(minutes=5)) == []                # not twice
    assert db.notify_targets(owner="alice")[0]["last_ok_at"]
    # a target whose service is no longer allowed is skipped with an error, not sent
    db.record_report(owner="alice", kind="week", label="2026-W46", result="x")
    nt.send_to_targets(db, "alice", allow_json.__class__(**{**allow_json.__dict__, "notify_allow": frozenset()}), "t", "b")
    assert "not allowed" in db.notify_targets(owner="alice")[0]["last_error"]


@pytest.fixture
def proxy(make_server, allow_json):
    return make_server(dataclasses.replace(allow_json, proxy_secret=SECRET))


def test_web_settings(proxy, db, catcher):
    h = {"X-Worklog-Proxy": SECRET, "Remote-User": "alice", "Origin": proxy}
    with httpx.Client(base_url=proxy, headers=h) as c:
        data = c.get("/ui/api/notify").json()
        assert data["targets"] == [] and data["schedule"]["time"] == "07:30" and data["services"]
        r = c.post("/ui/api/notify/targets", json={"url": "ntfy://127.0.0.1:9/my-secret-topic", "label": "Handy"})
        assert r.status_code == 201 and r.json()["target"] == "ntfy://…opic"
        assert c.post("/ui/api/notify/targets", json={"url": "xml://example.com/x"}).status_code == 400   # not allowed
        assert c.post("/ui/api/notify/targets", json={"url": catcher, "label": "Hook"}).status_code == 201  # allowed here
        listing = c.get("/ui/api/notify").text
        assert "my-secret-topic" not in listing and "127.0.0.1" not in listing                         # never unmasked
        hook = [t for t in c.get("/ui/api/notify").json()["targets"] if t["label"] == "Hook"][0]
        assert c.post(f"/ui/api/notify/targets/{hook['id']}/test", json={}).json()["last_ok_at"]
        assert Catcher.received[-1]["title"] == "Worklog test"
        r = c.post("/ui/api/notify/schedule", json={"weekly": True, "weekday": 0, "monthly": True, "time": "06:45"})
        assert r.json() == {"weekly": True, "weekday": 0, "monthly": True, "time": "06:45"}
        for bad in ({"weekly": "ja", "weekday": 0, "monthly": False, "time": "07:30"},
                    {"weekly": True, "weekday": 7, "monthly": False, "time": "07:30"},
                    {"weekly": True, "weekday": 0, "monthly": False, "time": "7:30"}):
            assert c.post("/ui/api/notify/schedule", json=bad).status_code == 400
        assert "sent 1, failed 1" == c.post("/ui/api/notify/send", json={"period": "last-week"}).json()["result"]  # nothing listens on port 9
        assert c.post("/ui/api/notify/send", json={"period": "yesterday"}).status_code == 400
    bob = {"X-Worklog-Proxy": SECRET, "Remote-User": "bob", "Origin": proxy}
    assert httpx.get(proxy + "/ui/api/notify", headers=bob).json()["targets"] == []
    assert httpx.post(proxy + f"/ui/api/notify/targets/{hook['id']}/delete", json={}, headers=bob).status_code == 404
    assert httpx.post(proxy + f"/ui/api/notify/targets/{hook['id']}/test", json={}, headers=bob).status_code == 404
    # a token sign-in, even with write rights, can't see or change notifications
    token = create_token(db, "alice", "laptop", ["read", "write"])
    with httpx.Client(base_url=proxy, headers={"Origin": proxy}) as c:
        c.post("/ui/login", json={"token": token})
        assert c.get("/ui/api/notify").status_code == 403
        assert c.post("/ui/api/notify/targets", json={"url": "ntfy://x-topic"}).status_code == 403
