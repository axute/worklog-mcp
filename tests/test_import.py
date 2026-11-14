"""worklog-import: parsers, secret filter, rules, summaries, preview and upload."""

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from worklog_import import cli
from worklog_import.rules import load_rules, parse_simple_yaml
from worklog_import.secrets import redact
from worklog_import.sources import claude_ai_sessions, claude_code_sessions, filter_sessions, parse_ts
from worklog_import.summarize import session_text, summarize_simple, to_entries

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)


def ts(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def write_cc_session(root: Path, sid: str, cwd="/home/me/website", prompts=("Bitte die Startseite überarbeiten",)):
    d = root / "-home-me-website"
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    m = 0
    for p in prompts:
        lines.append({"type": "user", "timestamp": ts(m), "cwd": cwd, "gitBranch": "main", "message": {"content": p}})
        lines.append({"type": "assistant", "timestamp": ts(m + 1), "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{cwd}/index.html"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "npm run build\nnpm test"}},
            {"type": "text", "text": "Erledigt."}]}})
        m += 10
    # noise that must be ignored
    lines.append({"type": "user", "isMeta": True, "timestamp": ts(m), "message": {"content": [{"type": "text", "text": "skill text"}]}})
    lines.append({"type": "user", "timestamp": ts(m), "message": {"content": [{"type": "tool_result", "content": "x"}]}})
    lines.append({"type": "user", "timestamp": ts(m), "message": {"content": "<local-command-stdout>x</local-command-stdout>"}})
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n", encoding="utf-8")
    sub = d / sid / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(json.dumps(lines[0]) + "\n")
    return d / f"{sid}.jsonl"


# ------------------------------------------------------------------ secrets

@pytest.mark.parametrize("text, kind", [
    ("token wl_" + "a" * 43, "worklog-token"),
    ("git push https://user:hunter2secret@github.com/x", "basic-auth-url"),
    ("export GITHUB_TOKEN=github_pat_" + "A1b2" * 10, "github-token"),
    ("Authorization: Bearer abcdefghijklmnop1234", "bearer"),
    ("password=Sup3rGeheim!", "secret"),
    ("api_key: 'abcd1234efgh'", "secret"),
    ("ANTHROPIC_API_KEY sk-ant-api03-" + "x" * 30, "anthropic-key"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----", "private-key"),
    ("jwt eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4", "jwt"),
])
def test_redact(text, kind):
    out, n = redact(text)
    assert n >= 1
    assert f"REDACTED:{kind}" in out


@pytest.mark.parametrize("text", [
    "Traefik-Router für website eingerichtet, Port 8080, Datei src/worklog/db.py geändert.",
    "Token angelegt und Passwort zurückgesetzt, secret santa geplant.",
    "Der API key wird über die Einstellungen gesetzt.",
    "Systeme mit OAuth: Booked Scheduler, MRBS und Kimai.",
])
def test_redact_keeps_normal_text(text):
    assert redact(text) == (text, 0)


# ------------------------------------------------------------------ rules

def test_rules_example_simple_parser_matches_pyyaml_shape():
    data = parse_simple_yaml((ROOT / "import-rules.example.yaml").read_text(encoding="utf-8"))
    assert data["default"] == "unsortiert"
    assert data["rules"][0] == {"project": "website", "paths": ["*/website*", "*/homepage*"],
                                "keywords": ["landing page", "start page"]}
    assert data["rules"][2]["keywords"] == ["docker compose", "reverse proxy", "traefik"]


def test_rules_match(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text((ROOT / "import-rules.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    rules = load_rules(f)
    write_cc_session(tmp_path / "p", "s1")
    s = next(claude_code_sessions(tmp_path / "p"))
    assert rules.match(s) == "website"  # by path
    s.cwd = "/elsewhere"
    s.turns[0].prompt = "Traefik Labels setzen"
    assert rules.match(s) == "homelab"  # by keyword
    s.turns[0].prompt = "Kuchenrezept"
    assert rules.match(s) is None and rules.default == "unsortiert"


# ------------------------------------------------------------------ parsers

def test_claude_code_parser(tmp_path):
    write_cc_session(tmp_path, "abc123", prompts=("Bitte die Startseite überarbeiten", "Und noch die Bilder komprimieren"))
    sessions = list(claude_code_sessions(tmp_path))
    assert len(sessions) == 1  # subagent transcript ignored
    s = sessions[0]
    assert s.external_base == "claude-code:abc123"
    assert [t.prompt for t in s.turns] == ["Bitte die Startseite überarbeiten", "Und noch die Bilder komprimieren"]
    assert s.turns[0].reply == "Erledigt."
    assert s.cwd == "/home/me/website" and s.branch == "main"
    assert s.files == ["/home/me/website/index.html"]
    assert s.commands[0] == "npm run build"
    assert s.start == T0 and s.end == T0 + timedelta(minutes=20)
    text = session_text(s)
    assert "USER: Bitte die Startseite" in text and "Changed files" in text


def test_claude_ai_parser_zip(tmp_path):
    conv = [
        {"uuid": "u-1", "name": "Docker Netzwerk", "created_at": "2026-09-01T10:00:00.123456Z",
         "chat_messages": [
             {"sender": "human", "text": "Wie richte ich ein Docker-Netz ein?", "created_at": "2026-09-01T10:00:00.123Z"},
             {"sender": "assistant", "content": [{"type": "text", "text": "So: docker network create"}], "created_at": "2026-09-01T10:01:00Z"},
         ]},
        {"uuid": "u-2", "name": "leer", "chat_messages": []},
    ]
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("data/conversations.json", json.dumps(conv))
    sessions = list(claude_ai_sessions(z))
    assert len(sessions) == 1
    s = sessions[0]
    assert s.external_base == "claude-ai:u-1" and s.title == "Docker Netzwerk"
    assert s.turns[0].reply == "So: docker network create"


def test_parse_ts_variants():
    assert parse_ts("2026-11-13T08:15:00.1Z") == datetime(2026, 11, 13, 8, 15, 0, 100000, tzinfo=timezone.utc)
    assert parse_ts("2026-11-13T08:15:00.123456789+01:00").utcoffset() == timedelta(hours=1)
    assert parse_ts("kaputt") is None


def test_filter_sessions(tmp_path):
    write_cc_session(tmp_path, "aaa111")
    s = next(claude_code_sessions(tmp_path))
    assert list(filter_sessions([s], None, None, 2, [])) == [s]
    assert list(filter_sessions([s], T0 + timedelta(days=1), None, 0, [])) == []
    assert list(filter_sessions([s], None, None, 0, ["aaa"])) == []
    s.end = datetime.now(timezone.utc)
    assert list(filter_sessions([s], None, None, 2, [])) == []  # probably still running


# ------------------------------------------------------------------ summaries

def test_simple_summary_and_entries(tmp_path):
    write_cc_session(tmp_path, "s1", prompts=("Passwort ist password=geheim123 bitte Startseite bauen",))
    s = next(claude_code_sessions(tmp_path))
    entries = to_entries(s, summarize_simple(s, "website"), "website")
    assert len(entries) == 1
    e = entries[0]
    assert e["external_id"] == "claude-code:s1:0"
    assert "geheim123" not in e["summary"] and "REDACTED" in e["summary"]
    assert e["project"] == "website" and e["session_id"] == "s1"


def test_to_entries_handles_model_output():
    from worklog_import.sources import Session, Turn
    s = Session("claude-code", "x", T0, T0, turns=[Turn(T0, "p")])
    result = {"skip": False, "entries": [
        {"ts": "2026-10-02T09:05:00Z", "summary": "A erledigt", "tags": ["CI", " "], "duration_min": 5},
        {"ts": "kein datum", "summary": "B mit token wl_" + "b" * 40, "project": "andere"},
        {"summary": "  "},
    ]}
    entries = to_entries(s, result, "p1")
    assert [e["external_id"] for e in entries] == ["claude-code:x:0", "claude-code:x:1"]
    assert entries[0]["tags"] == ["ci"] and entries[0]["project"] == "p1"
    assert entries[1]["ts"] == T0.isoformat() and entries[1]["project"] == "andere"
    assert "wl_" not in entries[1]["summary"]
    assert to_entries(s, {"skip": True, "entries": []}, "p1") == []


# ------------------------------------------------------------------ end to end: prepare + upload

def test_prepare_and_upload_twice(server, db, tokens, tmp_path, monkeypatch, capsys):
    projects = tmp_path / "projects"
    write_cc_session(projects, "sess-1")
    write_cc_session(projects, "sess-2", prompts=("Bilder komprimieren",))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["prepare", "--path", str(projects), "--summarizer", "none", "--min-age-hours", "0"]) == 0
    preview = [json.loads(l) for l in Path("import-preview.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {p["external_id"] for p in preview} == {"claude-code:sess-1:0", "claude-code:sess-2:0"}
    assert "# Import preview" in Path("import-preview.md").read_text(encoding="utf-8")
    assert json.loads(Path("import-cache.json").read_text())  # cached

    # user leaves one out
    lines = Path("import-preview.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0]); first["skip"] = True
    Path("import-preview.jsonl").write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n", encoding="utf-8")

    monkeypatch.setenv("WORKLOG_TOKEN", tokens["w"])
    assert cli.main(["upload", "--url", server]) == 0
    first_run = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first_run == {"created": 1, "updated": 0, "skipped": 0, "errors": 0}
    assert cli.main(["upload", "--url", server]) == 0
    second_run = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert second_run["created"] == 0 and second_run["skipped"] == 1

    entries = db.list_entries(limit=50)
    assert len(entries) == 1
    e = entries[0]
    assert e.source == "import" and e.agent == "hook-only" and e.ts == T0  # original time


def test_upload_needs_token(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKLOG_TOKEN", raising=False)
    (tmp_path / "p.jsonl").write_text("")
    with pytest.raises(SystemExit, match="WORKLOG_TOKEN"):
        cli.main(["upload", "--url", "http://x", "--file", str(tmp_path / "p.jsonl")])


def test_project_normalization_and_entry_rules(tmp_path):
    from worklog_import.rules import Rule, Rules, normalize_project
    from worklog_import.sources import Session, Turn
    assert normalize_project("Garten Bewässerung") == "garten-bewässerung"
    assert normalize_project(" label.presets ") == "label.presets"
    assert normalize_project("3D-Druck") == "3d-druck"
    rules = Rules(rules=[Rule("media-server", keywords=["jellyfin"]), Rule("garten", keywords=["bewässerung"])])
    s = Session("claude-ai", "c1", T0, T0, title="Haus", turns=[Turn(T0, "p")])
    result = {"skip": False, "entries": [
        {"ts": T0.isoformat(), "summary": "Jellyfin startet nur mit NAS-Mount", "project": "unsortiert"},
        {"ts": T0.isoformat(), "summary": "Neuer Rasenmäher bestellt", "project": "Garten Werkzeug"},
        {"ts": T0.isoformat(), "summary": "Etwas ganz anderes", "project": "unsortiert"},
    ]}
    entries = to_entries(s, result, "garten", rules)
    assert [e["project"] for e in entries] == ["media-server", "garten-werkzeug", "garten"]
    assert rules.projects() == ["media-server", "garten"]


def test_copilot_csv(tmp_path):
    from worklog_import.sources import copilot_sessions
    f = tmp_path / "copilot.csv"
    f.write_text(
        "\ufeffConversation,Time,Author,Message\n"
        "Gartenplanung,2026-03-02T09:05:00,AI,Antwort 2\n"
        "Gartenplanung,2026-03-02T09:00:00,Human,Welche Pflanzen?\n"
        "Gartenplanung,2026-03-02T09:00:00,AI,Antwort 1\n"
        "Gartenplanung,2026-03-02T09:04:00,Human,\"Und, mit Komma?\"\n"
        "Gartenplanung,2026-03-05T18:00:00,Human,Neue Frage Tage später\n"
        "Rezept,2026-03-03T12:00:00,Human,Kuchen\n"
        "Rezept,kaputt,Human,x\n",
        encoding="utf-8")
    sessions = sorted(copilot_sessions(f, tz="Europe/Berlin"), key=lambda s: s.start)
    assert [(s.title, len(s.turns)) for s in sessions] == [("Gartenplanung", 2), ("Rezept", 1), ("Gartenplanung", 1)]
    first = sessions[0]
    assert first.start == datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)  # 09:00 Berlin
    assert first.turns[0].reply == "Antwort 1" and first.turns[1].prompt == "Und, mit Komma?"
    assert first.external_base.startswith("copilot:")
    again = sorted(copilot_sessions(f, tz="Europe/Berlin"), key=lambda s: s.start)
    assert [s.external_base for s in again] == [s.external_base for s in sessions]  # stable ids
    with pytest.raises(ValueError, match="time zone"):
        list(copilot_sessions(f, tz="Mars/Base"))


def test_prepare_in_stages_with_max_new(tmp_path, monkeypatch, capsys):
    projects = tmp_path / "projects"
    for i in range(5):
        write_cc_session(projects, f"stage-{i}")
    monkeypatch.chdir(tmp_path)
    args = ["prepare", "--path", str(projects), "--summarizer", "none", "--min-age-hours", "0", "--max-new", "2"]
    counts = []
    for _ in range(4):
        cli.main(args)
        err = capsys.readouterr().err
        counts.append(len(Path("import-preview.jsonl").read_text(encoding="utf-8").splitlines()))
    assert counts == [2, 4, 5, 5]
    assert "all sessions summarized" in err


def test_aliases_merge_project_names(tmp_path):
    from worklog_import.sources import Session, Turn
    f = tmp_path / "r.yaml"
    f.write_text("default: unsortiert\nrules:\n  - project: media\n    keywords: [jellyfin]\n"
                 "aliases:\n  - project: search\n    match: [\"algolia*\", \"*-suche\"]\n"
                 "  - project: docs\n    match: [pandoc*, markdown*]\n", encoding="utf-8")
    rules = load_rules(f)
    assert rules.projects() == ["media", "search", "docs"]
    s = Session("copilot", "c", T0, T0, turns=[Turn(T0, "p")])
    res = {"skip": False, "entries": [{"ts": T0.isoformat(), "summary": "x", "project": p}
                                      for p in ["Algolia PHP", "webseiten-suche", "pandoc-pdf", "anderes"]]}
    assert [e["project"] for e in to_entries(s, res, "unsortiert", rules)] == ["search", "search", "docs", "anderes"]


def test_limit_stops_run(tmp_path, monkeypatch, capsys):
    from worklog_import import summarize
    projects = tmp_path / "projects"
    for i in range(3):
        write_cc_session(projects, f"lim-{i}")
    calls = []

    def fake(s, project, model=None, known=None, lean=False):
        calls.append(s.session_id)
        if len(calls) == 2:
            raise summarize.LimitReached("You've hit your 5-hour limit")
        return summarize.summarize_simple(s, project)

    monkeypatch.setattr(cli, "summarize_claude", fake)
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["prepare", "--path", str(projects), "--min-age-hours", "0"])
    assert rc == 2 and len(calls) == 2  # third session not tried
    assert "LIMIT REACHED" in capsys.readouterr().err
    assert len(Path("import-preview.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert summarize._is_limit("You've hit your 5-hour limit · resets 9:20am")
    assert summarize._is_limit("x", 429) and not summarize._is_limit("other error")
