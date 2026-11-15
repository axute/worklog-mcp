"""Read-only web view of the work log.

    GET  /ui/                 single page app (static files from worklog/static)
    POST /ui/login            {"token": "wl_..."} -> session cookie (token needs scope read)
    POST /ui/logout           clears the cookie
    GET  /ui/session          who is signed in
    GET  /api/entries         filtered list: q, project, agent, source, since, until, limit, offset
    GET  /api/facets          projects, agents and sources with entry counts
    GET  /api/stats           totals, per project and per day for the same filters

The read endpoints accept a bearer token or the session cookie. The cookie
holds a signed session, never the token, and gives read access only; writing
always needs a bearer token (so a cookie cannot be abused cross-site).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .auth import SESSION_COOKIE, SESSION_TTL, check_session, check_token, create_session
from .config import Settings
from .db import Database, as_dict
from .report import stats

log = logging.getLogger("worklog.webui")

STATIC = Path(__file__).parent / "static"
FILES = {
    "": ("index.html", "text/html; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "style.css": ("style.css", "text/css; charset=utf-8"),
    "favicon.svg": ("favicon.svg", "image/svg+xml"),
}
SECURITY_HEADERS = {
    # everything comes from this origin; no inline scripts, no framing
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store", **SECURITY_HEADERS})


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _same_origin(request: Request) -> bool:
    """Reject cross-site form posts: a browser sends Origin on POST."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # not a browser (curl, tests)
    return urlparse(origin).netloc == request.headers.get("host", "")


def _local(value: str | None, tz) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"'{value}' is not an ISO date or datetime") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def register_webui(mcp: FastMCP, db: Database, settings: Settings) -> None:
    tz = settings.timezone

    def reader(request: Request) -> tuple[str | None, JSONResponse | None]:
        """Agent name for a request with read access (bearer token or session cookie)."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            access = check_token(db, token.strip())
        else:
            access = check_session(db, request.cookies.get(SESSION_COOKIE))
        if access is None:
            return None, _json({"error": "not signed in"}, 401)
        if "read" not in access.scopes:
            return None, _json({"error": "token lacks the 'read' scope"}, 403)
        return access.client_id, None

    def filters(request: Request) -> dict:
        p = request.query_params
        return {
            "q": p.get("q") or None,
            "project": p.get("project") or None,
            "agent": p.get("agent") or None,
            "source": p.get("source") or None,
            "since": _local(p.get("since"), tz),
            "until": _local(p.get("until"), tz),
        }

    # ------------------------------------------------------------------ session

    @mcp.custom_route("/ui/login", methods=["POST"], include_in_schema=False)
    async def login(request: Request) -> Response:
        if not _same_origin(request):
            return _json({"error": "cross-site request"}, 403)
        try:
            body = json.loads(await request.body() or b"{}")
            token = str(body.get("token") or "").strip() if isinstance(body, dict) else ""
        except ValueError:
            token = ""
        session, access = create_session(db, token)
        if access is None:
            return _json({"error": "invalid token"}, 401)
        if session is None:
            return _json({"error": "token lacks the 'read' scope"}, 403)
        log.info("web_login", extra={"agent": access.client_id})
        response = _json({"agent": access.client_id})
        response.set_cookie(SESSION_COOKIE, session, max_age=int(SESSION_TTL.total_seconds()), path="/",
                            httponly=True, samesite="strict", secure=_is_https(request))
        return response

    @mcp.custom_route("/ui/logout", methods=["POST"], include_in_schema=False)
    async def logout(request: Request) -> Response:
        response = _json({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @mcp.custom_route("/ui/session", methods=["GET"], include_in_schema=False)
    async def session(request: Request) -> Response:
        agent, err = reader(request)
        return err or _json({"agent": agent, "timezone": str(tz)})

    # ------------------------------------------------------------------ data (read only)

    @mcp.custom_route("/api/entries", methods=["GET"], include_in_schema=False)
    async def get_entries(request: Request) -> Response:
        _, err = reader(request)
        if err:
            return err
        try:
            f = filters(request)
            limit = int(request.query_params.get("limit", "50"))
            offset = int(request.query_params.get("offset", "0"))
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        entries, total = db.query_entries(**f, limit=limit, offset=offset)
        return _json({"total": total, "offset": max(0, offset), "entries": [as_dict(e, tz) for e in entries]})

    @mcp.custom_route("/api/facets", methods=["GET"], include_in_schema=False)
    async def get_facets(request: Request) -> Response:
        _, err = reader(request)
        return err or _json(db.facets())

    @mcp.custom_route("/api/stats", methods=["GET"], include_in_schema=False)
    async def get_stats(request: Request) -> Response:
        _, err = reader(request)
        if err:
            return err
        try:
            f = filters(request)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        entries, _ = db.query_entries(**f, limit=None)
        return _json(stats(entries, tz))

    # ------------------------------------------------------------------ static files

    def static(name: str):
        filename, media_type = FILES[name]

        async def handler(request: Request) -> Response:
            return FileResponse(STATIC / filename, media_type=media_type,
                                headers={"Cache-Control": "no-cache", **SECURITY_HEADERS})
        return handler

    for name in FILES:
        mcp.custom_route(f"/ui/{name}", methods=["GET"], include_in_schema=False)(static(name))

    @mcp.custom_route("/ui", methods=["GET"], include_in_schema=False)
    async def ui_redirect(request: Request) -> Response:
        return RedirectResponse("/ui/", status_code=308)

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def root_redirect(request: Request) -> Response:
        return RedirectResponse("/ui/", status_code=307)
