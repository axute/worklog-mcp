from __future__ import annotations

import socket
import threading
import time
from zoneinfo import ZoneInfo

import pytest
import uvicorn

from worklog.auth import create_token
from worklog.config import Settings
from worklog.db import Database
from worklog.server import create_app


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "worklog.db")
    yield d
    d.close()


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "worklog.db"), host="127.0.0.1", port=0, timezone=ZoneInfo("Europe/Berlin"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def make_server(db):
    """Start real HTTP servers on localhost (as agents reach them) for given settings."""
    running = []

    def start(settings: Settings) -> str:
        port = _free_port()
        app = create_app(settings, db)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        srv = uvicorn.Server(config)
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()
        running.append((srv, thread))
        deadline = time.time() + 10
        while not srv.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return f"http://127.0.0.1:{port}"

    yield start
    for srv, thread in running:
        srv.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def server(make_server, settings):
    return make_server(settings)


@pytest.fixture
def tokens(db):
    return {
        "rw": create_token(db, "alice", "claude-code-laptop", ["write", "read"]),
        "w": create_token(db, "alice", "hook-only", ["write"]),
        "r": create_token(db, "alice", "reader", ["read"]),
    }
