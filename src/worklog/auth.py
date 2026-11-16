"""Bearer tokens: generation, hashing and verification.

Tokens look like ``wl_<43 url-safe characters>`` (32 random bytes). Only the
SHA-256 hash is stored. The token name becomes the ``agent`` of every entry
written with it, so an agent cannot write under another name.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from .db import Database, utcnow

TOKEN_PREFIX = "wl_"
NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
# last_used_at is written at most this often per token, to avoid a write per request
TOUCH_INTERVAL = timedelta(minutes=5)


class AuthError(PermissionError):
    """Raised by tools when the caller lacks a scope."""


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(db: Database, owner: str, name: str, scopes: list[str]) -> str:
    """Create a token for a user and return the plain text (shown once, never stored)."""
    name = name.strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("name: 1-64 characters, letters, digits, '-', '_' or '.'")
    token = generate_token()
    db.add_token(owner, name, hash_token(token), scopes)
    return token


def _access(record, token: str, scopes: list[str]) -> AccessToken:
    # client_id = agent name; the owner travels in the claims
    return AccessToken(token=token, client_id=record.name, scopes=scopes, subject=record.name,
                       claims={"owner": record.owner})


def owner_of(access: AccessToken) -> str:
    owner = (access.claims or {}).get("owner")
    if not owner:
        raise AuthError("token has no owner")
    return owner


def check_token(db: Database, token: str | None) -> AccessToken | None:
    """Return an AccessToken for a valid, unrevoked token, else None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    digest = hash_token(token)
    record = db.find_token_by_hash(digest)
    # compare again in constant time; the lookup itself only narrows down the row
    if record is None or not hmac.compare_digest(record.token_hash, digest) or record.revoked_at:
        return None
    now = utcnow()
    if record.last_used_at is None or now - record.last_used_at >= TOUCH_INTERVAL:
        db.touch_token(record.id, now)
    return _access(record, token, record.scopes)


# ---------------------------------------------------------------- web sessions

SESSION_COOKIE = "worklog_session"
SESSION_TTL = timedelta(days=30)


def _sign(db: Database, payload: str) -> str:
    return hmac.new(db.get_secret("session").encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session(db: Database, token: str | None) -> tuple[str | None, AccessToken | None]:
    """Exchange a valid token for a signed session value (the cookie never holds the token).
    Returns (None, None) for an invalid token and (None, access) when it lacks the read scope."""
    access = check_token(db, token)
    if access is None:
        return None, None
    if "read" not in access.scopes:
        return None, access
    record = db.get_token(owner_of(access), access.client_id)
    payload = f"v1.{record.id}.{int((utcnow() + SESSION_TTL).timestamp())}"
    return f"{payload}.{_sign(db, payload)}", access


def check_session(db: Database, value: str | None) -> AccessToken | None:
    """Session cookie -> AccessToken with read scope only; follows revocation of the token."""
    parts = (value or "").split(".")
    if len(parts) != 4 or parts[0] != "v1" or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    payload = ".".join(parts[:3])
    if not hmac.compare_digest(_sign(db, payload), parts[3]):
        return None
    if int(parts[2]) < utcnow().timestamp():
        return None
    record = db.get_token_by_id(int(parts[1]))
    if record is None or record.revoked_at or "read" not in record.scopes:
        return None
    return _access(record, "session", ["read"])


class WorklogTokenVerifier(TokenVerifier):
    """FastMCP auth provider backed by the tokens table."""

    def __init__(self, db: Database):
        super().__init__()
        self._db = db

    async def verify_token(self, token: str) -> AccessToken | None:
        return check_token(self._db, token)


def current_agent(required_scope: str) -> tuple[str, str]:
    """(owner, agent name) of the authenticated token for the running tool call; checks the scope."""
    token = get_access_token()
    if token is None:
        raise AuthError("not authenticated")
    if required_scope not in token.scopes:
        raise AuthError(f"token '{token.client_id}' lacks the '{required_scope}' scope")
    return owner_of(token), token.client_id
