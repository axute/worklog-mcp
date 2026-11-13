from datetime import datetime, timedelta, timezone

import pytest

from worklog.db import Database, from_db_ts, to_db_ts


def test_migration_sets_version_and_is_repeatable(tmp_path):
    path = tmp_path / "w.db"
    d = Database(path)
    assert d.schema_version() == 1
    d.close()
    d = Database(path)  # second open must not fail or re-run
    assert d.schema_version() == 1
    d.close()


def test_wal_mode(db):
    assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_add_and_get_entry(db):
    e = db.add_entry(agent="a", source="tool", project=" website ", summary=" Startseite überarbeitet ",
                     tags=["Update", "update", " docker "], duration_min=15, session_id="s1")
    assert e.id == 1
    assert e.project == "website"
    assert e.summary == "Startseite überarbeitet"
    assert e.tags == ["update", "docker"]
    assert e.duration_min == 15
    assert e.ts.tzinfo is not None
    assert abs((e.ts - datetime.now(timezone.utc)).total_seconds()) < 5


@pytest.mark.parametrize("kwargs, msg", [
    (dict(project="", summary="x"), "project"),
    (dict(project="p", summary="  "), "summary"),
    (dict(project="p", summary="x" * 301), "longer than 300"),
    (dict(project="p", summary="x", source="other"), "source"),
    (dict(project="p", summary="x", duration_min=-1), "negative"),
])
def test_add_entry_validation(db, kwargs, msg):
    args = dict(agent="a", source="tool")
    args.update(kwargs)
    with pytest.raises(ValueError, match=msg):
        db.add_entry(**args)


def test_summary_of_exactly_300_chars_is_fine(db):
    assert len(db.add_entry(agent="a", source="tool", project="p", summary="x" * 300).summary) == 300


def test_external_id_unique(db):
    db.add_entry(agent="a", source="import", project="p", summary="x", external_id="claude-code:1")
    with pytest.raises(Exception):
        db.add_entry(agent="a", source="import", project="p", summary="y", external_id="claude-code:1")


def test_original_ts_kept(db):
    old = datetime(2026, 3, 1, 9, 30, tzinfo=timezone.utc)
    e = db.add_entry(agent="a", source="import", project="p", summary="x", ts=old)
    assert e.ts == old
    assert e.created_at > old


def test_list_filters_and_order(db):
    base = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(5):
        db.add_entry(agent="a" if i % 2 else "b", source="tool", project="p1" if i < 3 else "p2",
                     summary=f"e{i}", ts=base + timedelta(days=i))
    assert [e.summary for e in db.list_entries()] == ["e4", "e3", "e2", "e1", "e0"]
    assert [e.summary for e in db.list_entries(project="p1")] == ["e2", "e1", "e0"]
    assert [e.summary for e in db.list_entries(agent="a")] == ["e3", "e1"]
    since, until = base + timedelta(days=1), base + timedelta(days=3)
    assert [e.summary for e in db.list_entries(since=since, until=until)] == ["e2", "e1"]
    assert len(db.list_entries(limit=2)) == 2
    assert len(db.list_entries(limit=10_000)) == 5  # clamped to 500, all 5 returned


def test_ts_roundtrip():
    dt = datetime(2026, 11, 13, 8, 15, 30, 123456, tzinfo=timezone.utc)
    assert to_db_ts(dt) == "2026-11-13T08:15:30Z"
    assert from_db_ts(to_db_ts(dt)) == dt.replace(microsecond=0)


def test_tokens(db):
    t = db.add_token("x", "h1", ["read", "write"])
    assert t.scopes == ["read", "write"]
    with pytest.raises(ValueError, match="already exists"):
        db.add_token("x", "h2", ["read"])
    with pytest.raises(ValueError, match="scopes"):
        db.add_token("y", "h3", ["admin"])
    assert db.find_token_by_hash("h1").name == "x"
    assert db.revoke_token("x").revoked_at is not None
    with pytest.raises(ValueError, match="already revoked"):
        db.revoke_token("x")
    with pytest.raises(KeyError):
        db.revoke_token("nope")
