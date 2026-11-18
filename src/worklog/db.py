"""SQLite storage: schema, migrations and queries (standard library only).

Every entry and token belongs to an owner (a user). All entry and token queries
take the owner as a required argument, so data of one user never shows up for
another; there is no query across owners except the token lookup by hash.

Timestamps are stored as ISO 8601 strings in UTC ("2026-11-13T08:15:00Z") so
that they sort and compare correctly as text.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SUMMARY_MAX = 300
SOURCES = ("tool", "hook", "import")
SCOPES = ("read", "write")

# Each migration runs once, in order; PRAGMA user_version records the last one.
MIGRATIONS: list[str] = [
    # 1: entries and tokens
    """
    CREATE TABLE entries (
        id           INTEGER PRIMARY KEY,
        ts           TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        agent        TEXT NOT NULL,
        source       TEXT NOT NULL CHECK (source IN ('tool', 'hook', 'import')),
        project      TEXT NOT NULL,
        summary      TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 300),
        details      TEXT,
        tags         TEXT,
        duration_min INTEGER CHECK (duration_min IS NULL OR duration_min >= 0),
        session_id   TEXT,
        external_id  TEXT UNIQUE
    );
    CREATE INDEX entries_ts      ON entries (ts DESC);
    CREATE INDEX entries_project ON entries (project, ts DESC);
    CREATE INDEX entries_agent   ON entries (agent, ts DESC);

    CREATE TABLE tokens (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE,
        token_hash   TEXT NOT NULL UNIQUE,
        scopes       TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at   TEXT
    );
    """,
    # 2: full text search over summary, details, project and tags (external content, kept in sync by triggers)
    """
    CREATE VIRTUAL TABLE entries_fts USING fts5(
        summary, details, project, tags,
        content='entries', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts (rowid, summary, details, project, tags)
        VALUES (new.id, new.summary, new.details, new.project, new.tags);
    END;
    CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
        INSERT INTO entries_fts (entries_fts, rowid, summary, details, project, tags)
        VALUES ('delete', old.id, old.summary, old.details, old.project, old.tags);
    END;
    CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
        INSERT INTO entries_fts (entries_fts, rowid, summary, details, project, tags)
        VALUES ('delete', old.id, old.summary, old.details, old.project, old.tags);
        INSERT INTO entries_fts (rowid, summary, details, project, tags)
        VALUES (new.id, new.summary, new.details, new.project, new.tags);
    END;
    INSERT INTO entries_fts (entries_fts) VALUES ('rebuild');
    """,
    # 3: small key/value store (e.g. the secret that signs web sessions)
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # 4: owners (multi-user); existing rows go to the legacy owner (see _migrate_owners)
    "_migrate_owners",
]

OWNER_SCHEMA = """
    DROP TRIGGER IF EXISTS entries_ai;
    DROP TRIGGER IF EXISTS entries_ad;
    DROP TRIGGER IF EXISTS entries_au;
    CREATE TABLE entries_new (
        id           INTEGER PRIMARY KEY,
        owner        TEXT NOT NULL,
        ts           TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        agent        TEXT NOT NULL,
        source       TEXT NOT NULL CHECK (source IN ('tool', 'hook', 'import')),
        project      TEXT NOT NULL,
        summary      TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 300),
        details      TEXT,
        tags         TEXT,
        duration_min INTEGER CHECK (duration_min IS NULL OR duration_min >= 0),
        session_id   TEXT,
        external_id  TEXT,
        UNIQUE (owner, external_id)
    );
    INSERT INTO entries_new (id, owner, ts, created_at, agent, source, project, summary, details, tags,
                             duration_min, session_id, external_id)
        SELECT id, :owner, ts, created_at, agent, source, project, summary, details, tags,
               duration_min, session_id, external_id FROM entries;
    DROP TABLE entries;
    ALTER TABLE entries_new RENAME TO entries;
    CREATE INDEX entries_owner_ts      ON entries (owner, ts DESC);
    CREATE INDEX entries_owner_project ON entries (owner, project, ts DESC);
    CREATE INDEX entries_owner_agent   ON entries (owner, agent, ts DESC);
    CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts (rowid, summary, details, project, tags)
        VALUES (new.id, new.summary, new.details, new.project, new.tags);
    END;
    CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
        INSERT INTO entries_fts (entries_fts, rowid, summary, details, project, tags)
        VALUES ('delete', old.id, old.summary, old.details, old.project, old.tags);
    END;
    CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
        INSERT INTO entries_fts (entries_fts, rowid, summary, details, project, tags)
        VALUES ('delete', old.id, old.summary, old.details, old.project, old.tags);
        INSERT INTO entries_fts (rowid, summary, details, project, tags)
        VALUES (new.id, new.summary, new.details, new.project, new.tags);
    END;
    INSERT INTO entries_fts (entries_fts) VALUES ('rebuild');

    CREATE TABLE tokens_new (
        id           INTEGER PRIMARY KEY,
        owner        TEXT NOT NULL,
        name         TEXT NOT NULL,
        token_hash   TEXT NOT NULL UNIQUE,
        scopes       TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at   TEXT,
        UNIQUE (owner, name)
    );
    INSERT INTO tokens_new (id, owner, name, token_hash, scopes, created_at, last_used_at, revoked_at)
        SELECT id, :owner, name, token_hash, scopes, created_at, last_used_at, revoked_at FROM tokens;
    DROP TABLE tokens;
    ALTER TABLE tokens_new RENAME TO tokens;
"""

OWNER_MAX = 128


def clean_owner(owner: str | None) -> str:
    """User name as given by the sign-in (e.g. Authelia's Remote-User); no control characters."""
    o = (owner or "").strip()
    if not o or len(o) > OWNER_MAX or any(ord(c) < 32 or ord(c) == 127 for c in o):
        raise ValueError(f"owner: 1-{OWNER_MAX} printable characters")
    return o

BULK_MAX = 500


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db_ts(dt: datetime) -> str:
    """Datetime -> UTC ISO string with second precision. Naive values are treated as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_db_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_ts(value: str) -> datetime:
    """Parse user input: ISO date or datetime, with or without offset (naive = UTC)."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Entry:
    id: int
    owner: str
    ts: datetime
    created_at: datetime
    agent: str
    source: str
    project: str
    summary: str
    details: str | None
    tags: list[str]
    duration_min: int | None
    session_id: str | None
    external_id: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Entry":
        return cls(
            id=r["id"],
            owner=r["owner"],
            ts=from_db_ts(r["ts"]),
            created_at=from_db_ts(r["created_at"]),
            agent=r["agent"],
            source=r["source"],
            project=r["project"],
            summary=r["summary"],
            details=r["details"],
            tags=json.loads(r["tags"]) if r["tags"] else [],
            duration_min=r["duration_min"],
            session_id=r["session_id"],
            external_id=r["external_id"],
        )


@dataclass
class TokenRecord:
    id: int
    owner: str
    name: str
    token_hash: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "TokenRecord":
        return cls(
            id=r["id"],
            owner=r["owner"],
            name=r["name"],
            token_hash=r["token_hash"],
            scopes=[s for s in r["scopes"].split(",") if s],
            created_at=from_db_ts(r["created_at"]),
            last_used_at=from_db_ts(r["last_used_at"]) if r["last_used_at"] else None,
            revoked_at=from_db_ts(r["revoked_at"]) if r["revoked_at"] else None,
        )


class Database:
    """Thin wrapper around one SQLite connection. Safe to share between threads."""

    def __init__(self, path: str | Path, legacy_owner: str | None = None):
        """legacy_owner: owner for data from before multi-user (only used by migration 4)."""
        self.path = str(path)
        self._legacy_owner = legacy_owner
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.migrate()

    # ------------------------------------------------------------------ schema

    def schema_version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        with self._lock:
            current = self.schema_version()
            for number, sql in enumerate(MIGRATIONS, start=1):
                if number <= current:
                    continue
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    if sql == "_migrate_owners":
                        self._migrate_owners()
                    else:
                        for statement in _split_sql(sql):
                            self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {number}")
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    def _migrate_owners(self) -> None:
        has_data = self._conn.execute(
            "SELECT EXISTS (SELECT 1 FROM entries) OR EXISTS (SELECT 1 FROM tokens)").fetchone()[0]
        if has_data and not (self._legacy_owner or "").strip():
            raise RuntimeError("this database has entries or tokens from before multi-user support: "
                               "set WORKLOG_LEGACY_OWNER=<user name> once to assign them to that user")
        owner = clean_owner(self._legacy_owner) if has_data else ""
        for statement in _split_sql(OWNER_SCHEMA):
            self._conn.execute(statement, {"owner": owner} if ":owner" in statement else ())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ entries

    def add_entry(
        self,
        *,
        owner: str,
        agent: str,
        source: str,
        project: str,
        summary: str,
        details: str | None = None,
        tags: Iterable[str] | None = None,
        duration_min: int | None = None,
        session_id: str | None = None,
        ts: datetime | None = None,
        external_id: str | None = None,
    ) -> Entry:
        project = (project or "").strip()
        summary = (summary or "").strip()
        if not project:
            raise ValueError("project must not be empty")
        if not summary:
            raise ValueError("summary must not be empty")
        if len(summary) > SUMMARY_MAX:
            raise ValueError(f"summary is longer than {SUMMARY_MAX} characters ({len(summary)})")
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}")
        if duration_min is not None and duration_min < 0:
            raise ValueError("duration_min must not be negative")
        tag_list = _clean_tags(tags)
        owner = clean_owner(owner)
        now = utcnow()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO entries (owner, ts, created_at, agent, source, project, summary, details,
                                        tags, duration_min, session_id, external_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner,
                    to_db_ts(ts or now),
                    to_db_ts(now),
                    agent,
                    source,
                    project,
                    summary,
                    (details or None),
                    json.dumps(tag_list, ensure_ascii=False) if tag_list else None,
                    duration_min,
                    session_id or None,
                    external_id or None,
                ),
            )
            return self.get_entry(cur.lastrowid)

    def get_entry(self, entry_id: int) -> Entry:
        row = self._conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return Entry.from_row(row)

    def get_own_entry(self, *, owner: str, entry_id: int) -> Entry:
        with self._lock:
            row = self._conn.execute("SELECT * FROM entries WHERE owner = ? AND id = ?",
                                     (clean_owner(owner), int(entry_id))).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return Entry.from_row(row)

    def update_entry(self, *, owner: str, entry_id: int, agent: str | None = None, **changes: Any) -> Entry:
        """Change project, summary, details, tags or duration_min of an own entry.
        With `agent`, only entries written by that agent can be changed (used by the MCP tool).
        Only the given fields change; details=None/"" and duration_min=None clear them."""
        unknown = set(changes) - {"project", "summary", "details", "tags", "duration_min"}
        if unknown:
            raise ValueError(f"cannot change: {', '.join(sorted(unknown))}")
        if not changes:
            raise ValueError("nothing to change")
        sets: dict[str, Any] = {}
        if "project" in changes:
            project = str(changes["project"] or "").strip()
            if not project:
                raise ValueError("project must not be empty")
            sets["project"] = project
        if "summary" in changes:
            summary = str(changes["summary"] or "").strip()
            if not summary or len(summary) > SUMMARY_MAX:
                raise ValueError(f"summary must have 1-{SUMMARY_MAX} characters")
            sets["summary"] = summary
        if "details" in changes:
            d = changes["details"]
            sets["details"] = (str(d).strip() or None) if d is not None else None
        if "tags" in changes:
            tags = changes["tags"]
            if tags is not None and not isinstance(tags, list):
                raise ValueError("tags must be a list")
            tag_list = _clean_tags(tags)
            sets["tags"] = json.dumps(tag_list, ensure_ascii=False) if tag_list else None
        if "duration_min" in changes:
            d = changes["duration_min"]
            if d is not None and (not isinstance(d, int) or isinstance(d, bool) or d < 0):
                raise ValueError("duration_min must be a non-negative integer")
            sets["duration_min"] = d
        where, args = "owner = ? AND id = ?", [clean_owner(owner), int(entry_id)]
        if agent is not None:
            where += " AND agent = ?"
            args.append(agent)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE entries SET {', '.join(f'{k} = ?' for k in sets)} WHERE {where}",
                [*sets.values(), *args])
            if cur.rowcount == 0:
                raise KeyError(entry_id)
        return self.get_own_entry(owner=owner, entry_id=entry_id)

    def delete_entry(self, *, owner: str, entry_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM entries WHERE owner = ? AND id = ?",
                                     (clean_owner(owner), int(entry_id)))
        if cur.rowcount == 0:
            raise KeyError(entry_id)

    def rename_project(self, *, owner: str, old: str, new: str) -> int:
        """Move all entries of project `old` to `new` (renames, or merges into an existing project).
        Returns the number of moved entries."""
        new = (new or "").strip()
        if not new:
            raise ValueError("new project name must not be empty")
        if new == old:
            return 0
        with self._lock:
            cur = self._conn.execute("UPDATE entries SET project = ? WHERE owner = ? AND project = ?",
                                     (new, clean_owner(owner), old))
        if cur.rowcount == 0:
            raise KeyError(old)
        return cur.rowcount

    def list_entries(
        self,
        *,
        owner: str,
        project: str | None = None,
        agent: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
    ) -> list[Entry]:
        where, args = ["owner = ?"], [clean_owner(owner)]
        if project:
            where.append("project = ?")
            args.append(project)
        if agent:
            where.append("agent = ?")
            args.append(agent)
        if since:
            where.append("ts >= ?")
            args.append(to_db_ts(since))
        if until:
            where.append("ts < ?")
            args.append(to_db_ts(until))
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM entries WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        return [Entry.from_row(r) for r in rows]

    def search_entries(self, query: str, *, owner: str, project: str | None = None, limit: int = 20) -> list[Entry]:
        """Full text search. Every word must occur (prefix match); ranked by relevance, then newest."""
        match = fts_query(query)
        if not match:
            raise ValueError("query must contain at least one word")
        sql = ("SELECT e.* FROM entries_fts f JOIN entries e ON e.id = f.rowid "
               "WHERE entries_fts MATCH ? AND e.owner = ?")
        args: list[Any] = [match, clean_owner(owner)]
        if project:
            sql += " AND e.project = ?"
            args.append(project)
        sql += " ORDER BY bm25(entries_fts, 10.0, 3.0, 2.0, 2.0), e.ts DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Entry.from_row(r) for r in rows]

    def query_entries(
        self,
        *,
        owner: str,
        q: str | None = None,
        project: str | None = None,
        agent: str | None = None,
        source: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> tuple[list[Entry], int]:
        """Filtered entries, newest first, plus the total number of matches (for the web UI).
        q is a full text query (all words, prefix match); limit=None returns everything."""
        base = "FROM entries e"
        where: list[str] = ["e.owner = ?"]
        args: list[Any] = [clean_owner(owner)]
        if q and q.strip():
            match = fts_query(q)
            if not match:
                return [], 0  # only punctuation: nothing can match
            base = "FROM entries_fts f JOIN entries e ON e.id = f.rowid"
            where.append("entries_fts MATCH ?")
            args.append(match)
        for column, value in (("project", project), ("agent", agent), ("source", source)):
            if value:
                where.append(f"e.{column} = ?")
                args.append(value)
        if since:
            where.append("e.ts >= ?")
            args.append(to_db_ts(since))
        if until:
            where.append("e.ts < ?")
            args.append(to_db_ts(until))
        cond = " WHERE " + " AND ".join(where)
        sql = f"SELECT e.* {base}{cond} ORDER BY e.ts DESC, e.id DESC"
        page_args = list(args)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            page_args += [max(1, min(int(limit), 500)), max(0, int(offset))]
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) {base}{cond}", args).fetchone()[0]
            rows = self._conn.execute(sql, page_args).fetchall()
        return [Entry.from_row(r) for r in rows], total

    def facets(self, *, owner: str) -> dict[str, list[dict[str, Any]]]:
        """Values to filter by, with entry counts."""
        owner = clean_owner(owner)
        out: dict[str, list[dict[str, Any]]] = {}
        with self._lock:
            for name, column in (("projects", "project"), ("agents", "agent"), ("sources", "source")):
                rows = self._conn.execute(
                    f"SELECT {column} AS v, COUNT(*) AS n FROM entries WHERE owner = ? "
                    f"GROUP BY {column} ORDER BY {column} COLLATE NOCASE", (owner,)
                ).fetchall()
                out[name] = [{"name": r["v"], "entries": r["n"]} for r in rows]
        return out

    def entries_between(self, since: datetime, until: datetime, *, owner: str,
                        project: str | None = None) -> list[Entry]:
        """All entries in [since, until), oldest first (for summaries and exports)."""
        sql = "SELECT * FROM entries WHERE owner = ? AND ts >= ? AND ts < ?"
        args: list[Any] = [clean_owner(owner), to_db_ts(since), to_db_ts(until)]
        if project:
            sql += " AND project = ?"
            args.append(project)
        sql += " ORDER BY ts, id"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [Entry.from_row(r) for r in rows]

    def list_projects(self, *, owner: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT project, COUNT(*) AS n, MAX(ts) AS last_ts, SUM(duration_min) AS minutes "
                "FROM entries WHERE owner = ? GROUP BY project ORDER BY last_ts DESC", (clean_owner(owner),)
            ).fetchall()
        return [{"project": r["project"], "entries": r["n"], "last_ts": from_db_ts(r["last_ts"]),
                 "duration_min": r["minutes"] or 0} for r in rows]

    def import_entries(self, owner: str, agent: str, items: list[dict[str, Any]], *,
                       update: bool = False) -> dict[str, Any]:
        """Bulk import with external_id. Known ids are skipped, or overwritten with update=True.

        Each item: external_id, ts, project, summary, optional details, tags,
        duration_min, session_id. Invalid items are reported, the rest is written
        in one transaction."""
        if len(items) > BULK_MAX:
            raise ValueError(f"at most {BULK_MAX} entries per request")
        result: dict[str, Any] = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
        owner = clean_owner(owner)
        now = to_db_ts(utcnow())
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for i, item in enumerate(items):
                    try:
                        row = _import_row(item)
                    except (ValueError, TypeError, KeyError) as e:
                        result["errors"].append({"index": i, "error": str(e)})
                        continue
                    existing = self._conn.execute(
                        "SELECT id FROM entries WHERE owner = ? AND external_id = ?", (owner, row["external_id"])
                    ).fetchone()
                    if existing and not update:
                        result["skipped"] += 1
                    elif existing:
                        self._conn.execute(
                            """UPDATE entries SET ts=?, agent=?, project=?, summary=?, details=?, tags=?,
                                   duration_min=?, session_id=?, source='import' WHERE id=?""",
                            (row["ts"], agent, row["project"], row["summary"], row["details"], row["tags"],
                             row["duration_min"], row["session_id"], existing["id"]),
                        )
                        result["updated"] += 1
                    else:
                        self._conn.execute(
                            """INSERT INTO entries (owner, ts, created_at, agent, source, project, summary, details,
                                                    tags, duration_min, session_id, external_id)
                               VALUES (?, ?, ?, ?, 'import', ?, ?, ?, ?, ?, ?, ?)""",
                            (owner, row["ts"], now, agent, row["project"], row["summary"], row["details"], row["tags"],
                             row["duration_min"], row["session_id"], row["external_id"]),
                        )
                        result["created"] += 1
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return result

    def backup(self, target: str | Path) -> None:
        """Consistent copy via SQLite's online backup API (safe while the server runs)."""
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        dest = sqlite3.connect(str(target))
        try:
            with self._lock:
                self._conn.backup(dest)
        finally:
            dest.close()

    # ------------------------------------------------------------------ tokens

    def add_token(self, owner: str, name: str, token_hash: str, scopes: Iterable[str]) -> TokenRecord:
        owner = clean_owner(owner)
        scope_list = sorted(set(scopes))
        bad = [s for s in scope_list if s not in SCOPES]
        if bad or not scope_list:
            raise ValueError(f"scopes must be a non-empty subset of {', '.join(SCOPES)}")
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO tokens (owner, name, token_hash, scopes, created_at) VALUES (?, ?, ?, ?, ?)",
                    (owner, name, token_hash, ",".join(scope_list), to_db_ts(utcnow())),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"a token named '{name}' already exists") from e
            return self.get_token(owner, name)

    def get_token(self, owner: str, name: str) -> TokenRecord:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tokens WHERE owner = ? AND name = ?",
                                     (clean_owner(owner), name)).fetchone()
        if row is None:
            raise KeyError(name)
        return TokenRecord.from_row(row)

    def get_token_by_id(self, token_id: int) -> TokenRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tokens WHERE id = ?", (token_id,)).fetchone()
        return TokenRecord.from_row(row) if row else None

    def get_secret(self, name: str) -> str:
        """Random secret kept in the database, created on first use."""
        import secrets
        with self._lock:
            self._conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                               (f"secret:{name}", secrets.token_hex(32)))
            return self._conn.execute("SELECT value FROM meta WHERE key = ?", (f"secret:{name}",)).fetchone()[0]

    def find_token_by_hash(self, token_hash: str) -> TokenRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        return TokenRecord.from_row(row) if row else None

    def list_tokens(self, *, owner: str | None) -> list[TokenRecord]:
        """Tokens of one owner; owner=None lists all (only for the admin command line)."""
        with self._lock:
            if owner is None:
                rows = self._conn.execute("SELECT * FROM tokens ORDER BY owner, name").fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM tokens WHERE owner = ? ORDER BY name",
                                          (clean_owner(owner),)).fetchall()
        return [TokenRecord.from_row(r) for r in rows]

    def revoke_token(self, owner: str, name: str) -> TokenRecord:
        owner = clean_owner(owner)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE owner = ? AND name = ? AND revoked_at IS NULL",
                (to_db_ts(utcnow()), owner, name),
            )
            if cur.rowcount == 0:
                self.get_token(owner, name)  # raises KeyError when unknown
                raise ValueError(f"token '{name}' is already revoked")
            return self.get_token(owner, name)

    def touch_token(self, token_id: int, when: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tokens SET last_used_at = ? WHERE id = ?", (to_db_ts(when or utcnow()), token_id)
            )


def fts_query(query: str) -> str:
    """User text -> safe FTS5 query: every word as a quoted prefix term, all required."""
    words = re.findall(r"\w+", query or "", flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)


def _import_row(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise TypeError("entry must be an object")
    external_id = str(item.get("external_id") or "").strip()
    if not external_id or len(external_id) > 200:
        raise ValueError("external_id is required (max. 200 characters)")
    if not item.get("ts"):
        raise ValueError("ts is required")
    project = str(item.get("project") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if not project:
        raise ValueError("project must not be empty")
    if not summary or len(summary) > SUMMARY_MAX:
        raise ValueError(f"summary must have 1-{SUMMARY_MAX} characters")
    duration = item.get("duration_min")
    if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool) or duration < 0):
        raise ValueError("duration_min must be a non-negative integer")
    tags = item.get("tags")
    if tags is not None and not isinstance(tags, list):
        raise TypeError("tags must be a list")
    tag_list = _clean_tags(tags)
    return {
        "external_id": external_id,
        "ts": to_db_ts(parse_ts(str(item["ts"]))),
        "project": project,
        "summary": summary,
        "details": (str(item["details"]) if item.get("details") else None),
        "tags": json.dumps(tag_list, ensure_ascii=False) if tag_list else None,
        "duration_min": duration,
        "session_id": (str(item["session_id"]) if item.get("session_id") else None),
    }


def _clean_tags(tags: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        t = str(t).strip().lower()
        if t and t not in out:
            out.append(t)
    return out


def _split_sql(sql: str) -> list[str]:
    """Split a migration into statements; keeps CREATE TRIGGER ... BEGIN ... END; together."""
    out: list[str] = []
    buf = ""
    for part in sql.split(";"):
        buf += part + ";"
        if sqlite3.complete_statement(buf):
            if buf.strip(" \n;"):
                out.append(buf.strip())
            buf = ""
    if buf.strip(" \n;"):
        out.append(buf.strip())
    return out


def as_dict(entry: Entry, tz: Any) -> dict[str, Any]:
    """Entry as a JSON-friendly dict with times in the display time zone."""
    return {
        "id": entry.id,
        "ts": entry.ts.astimezone(tz).isoformat(),
        "agent": entry.agent,
        "source": entry.source,
        "project": entry.project,
        "summary": entry.summary,
        "details": entry.details,
        "tags": entry.tags,
        "duration_min": entry.duration_min,
        "session_id": entry.session_id,
    }
