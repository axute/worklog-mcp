"""Web view of the work log.

    GET  /ui/                       single page app (static files from worklog/static)
    GET  /ui/session                who is signed in and whether they may manage tokens
    POST /ui/login                  {"token": "wl_..."} -> session cookie (token needs scope read)
    POST /ui/logout                 clears the cookie
    GET  /ui/api/entries            filtered list: q, project, agent, source, since, until, limit, offset
    GET  /ui/api/facets             projects, agents and sources with entry counts
    GET  /ui/api/stats              totals, per project and per day for the same filters
    GET  /ui/api/tokens             token list (admin)
    POST /ui/api/tokens             {"name", "scopes"} -> new token, shown once (admin)
    POST /ui/api/tokens/{name}/revoke                                             (admin)

    GET  /api/entries, /api/facets, /api/stats   the same reads for scripts, bearer token only

Who may do what:
* Signed in through the reverse proxy (WORKLOG_PROXY_SECRET set, e.g. Traefik with
  Authelia forward auth): the user name comes from a header the proxy sets; this
  user reads everything and manages tokens. Without the shared secret in
  X-Worklog-Proxy the header is ignored, so nobody can fake it by talking to the
  container directly.
* Token sign-in: a signed session cookie (never the token itself), read only.
* Bearer token with scope read: read only.
Changes need a request from the page's own origin with a JSON body.
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .auth import NAME_RE, SESSION_COOKIE, SESSION_TTL, check_session, check_token, create_session, create_token
from .config import Settings
from .db import SCOPES, Database, as_dict
from .report import stats

log = logging.getLogger("worklog.webui")

PROXY_HEADER = "x-worklog-proxy"
PROXY_SECRET_MIN = 24
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


@dataclass(frozen=True)
class Viewer:
    name: str
    via: str          # proxy | session | bearer
    admin: bool


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store", **SECURITY_HEADERS})


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _same_origin(request: Request, required: bool = False) -> bool:
    """Reject cross-site posts: browsers send Origin on POST. Clients without a
    browser (curl, tests) send none; that is fine unless `required`."""
    origin = request.headers.get("origin")
    if not origin:
        return not required
    return urlparse(origin).netloc == request.headers.get("host", "")


def _iso(dt: datetime | None, tz) -> str | None:
    return dt.astimezone(tz).isoformat() if dt else None


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
    if settings.proxy_secret and len(settings.proxy_secret) < PROXY_SECRET_MIN:
        raise ValueError(f"WORKLOG_PROXY_SECRET must have at least {PROXY_SECRET_MIN} characters")

    def proxy_user(request: Request) -> str | None:
        if not settings.proxy_secret:
            return None
        sent = request.headers.get(PROXY_HEADER, "")
        if not sent or not hmac.compare_digest(sent.encode(), settings.proxy_secret.encode()):
            return None
        user = request.headers.get(settings.user_header, "").strip()
        if not user or (settings.web_users and user not in settings.web_users):
            return None
        return user

    def viewer(request: Request, allow_cookie: bool = True) -> tuple[Viewer | None, JSONResponse | None]:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer":
            access, via = check_token(db, token.strip()), "bearer"
        else:
            user = proxy_user(request) if allow_cookie else None
            if user:
                return Viewer(user, "proxy", admin=True), None
            access, via = (check_session(db, request.cookies.get(SESSION_COOKIE)) if allow_cookie else None), "session"
        if access is None:
            return None, _json({"error": "not signed in"}, 401)
        if "read" not in access.scopes:
            return None, _json({"error": "token lacks the 'read' scope"}, 403)
        return Viewer(access.client_id, via, admin=False), None

    def admin(request: Request, change: bool) -> tuple[Viewer | None, JSONResponse | None]:
        who, err = viewer(request)
        if err:
            return None, err
        if not who.admin:
            return None, _json({"error": "only users signed in through the proxy may manage tokens"}, 403)
        if change and not (_same_origin(request, required=True)
                           and request.headers.get("content-type", "").split(";")[0].strip() == "application/json"):
            return None, _json({"error": "cross-site request"}, 403)
        return who, None

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
        response = _json({"user": access.client_id})
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
        who, err = viewer(request)
        if err:
            return err
        return _json({"user": who.name, "via": who.via, "admin": who.admin, "timezone": str(tz),
                      "logout_url": settings.logout_url if who.via == "proxy" else ""})

    # ------------------------------------------------------------------ data (read only)

    def reads(prefix: str, allow_cookie: bool) -> None:
        @mcp.custom_route(f"{prefix}/entries", methods=["GET"], include_in_schema=False)
        async def get_entries(request: Request) -> Response:
            _, err = viewer(request, allow_cookie)
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

        @mcp.custom_route(f"{prefix}/facets", methods=["GET"], include_in_schema=False)
        async def get_facets(request: Request) -> Response:
            _, err = viewer(request, allow_cookie)
            return err or _json(db.facets())

        @mcp.custom_route(f"{prefix}/stats", methods=["GET"], include_in_schema=False)
        async def get_stats(request: Request) -> Response:
            _, err = viewer(request, allow_cookie)
            if err:
                return err
            try:
                f = filters(request)
            except ValueError as e:
                return _json({"error": str(e)}, 400)
            entries, _ = db.query_entries(**f, limit=None)
            return _json(stats(entries, tz))

    reads("/ui/api", allow_cookie=True)
    reads("/api", allow_cookie=False)   # scripts: bearer only, so cookies never reach /api

    # ------------------------------------------------------------------ tokens (admin)

    def token_dict(r) -> dict:
        return {"name": r.name, "scopes": r.scopes, "created_at": _iso(r.created_at, tz),
                "last_used_at": _iso(r.last_used_at, tz), "revoked_at": _iso(r.revoked_at, tz)}

    @mcp.custom_route("/ui/api/tokens", methods=["GET"], include_in_schema=False)
    async def list_tokens(request: Request) -> Response:
        _, err = admin(request, change=False)
        return err or _json({"scopes": list(SCOPES), "tokens": [token_dict(r) for r in db.list_tokens()]})

    @mcp.custom_route("/ui/api/tokens", methods=["POST"], include_in_schema=False)
    async def new_token(request: Request) -> Response:
        who, err = admin(request, change=True)
        if err:
            return err
        try:
            body = json.loads(await request.body() or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            name = str(body.get("name") or "").strip()
            scopes = body.get("scopes")
            if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
                raise ValueError("scopes must be a list")
            token = create_token(db, name, scopes)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        log.info("token_created", extra={"agent": name, "by": who.name, "scopes": ",".join(sorted(set(scopes)))})
        return _json({"token": token, **token_dict(db.get_token(name))}, 201)

    @mcp.custom_route("/ui/api/tokens/{name}/revoke", methods=["POST"], include_in_schema=False)
    async def revoke_token(request: Request) -> Response:
        who, err = admin(request, change=True)
        if err:
            return err
        name = request.path_params["name"]
        if not NAME_RE.fullmatch(name):
            return _json({"error": "unknown token"}, 404)
        try:
            record = db.revoke_token(name)
        except KeyError:
            return _json({"error": "unknown token"}, 404)
        except ValueError as e:
            return _json({"error": str(e)}, 409)
        log.info("token_revoked", extra={"agent": name, "by": who.name})
        return _json(token_dict(record))

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
