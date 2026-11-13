from datetime import timedelta

import pytest

from worklog.auth import TOKEN_PREFIX, check_token, create_token, hash_token
from worklog.db import utcnow


def test_token_format_and_storage(db):
    token = create_token(db, "claude-code-laptop", ["write", "read"])
    assert token.startswith(TOKEN_PREFIX)
    assert len(token) >= len(TOKEN_PREFIX) + 43  # 32 random bytes, url-safe base64
    rec = db.get_token("claude-code-laptop")
    assert rec.token_hash == hash_token(token)
    # the plain token is stored nowhere
    dump = "\n".join(db._conn.iterdump())
    assert token not in dump


def test_check_token(db):
    token = create_token(db, "agent-1", ["write"])
    at = check_token(db, token)
    assert at.client_id == "agent-1"
    assert at.scopes == ["write"]
    assert check_token(db, token + "x") is None
    assert check_token(db, "nope") is None
    assert check_token(db, None) is None
    assert check_token(db, "") is None


def test_revoked_token_rejected(db):
    token = create_token(db, "agent-1", ["write"])
    db.revoke_token("agent-1")
    assert check_token(db, token) is None


def test_last_used_is_throttled(db):
    token = create_token(db, "agent-1", ["read"])
    check_token(db, token)
    first = db.get_token("agent-1").last_used_at
    assert first is not None
    check_token(db, token)
    assert db.get_token("agent-1").last_used_at == first
    db.touch_token(db.get_token("agent-1").id, utcnow() - timedelta(minutes=10))
    check_token(db, token)
    assert db.get_token("agent-1").last_used_at > first - timedelta(minutes=10)


@pytest.mark.parametrize("name", ["", "a b", "x" * 65, "ä"])
def test_invalid_names(db, name):
    with pytest.raises(ValueError):
        create_token(db, name, ["read"])
