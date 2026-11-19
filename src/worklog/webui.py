"""Web view of the work log.

    GET  /ui/                       single page app (static files from worklog/static)
    GET  /ui/session                who is signed in and whether they may manage tokens
    POST /ui/login                  {"token": "wl_..."} -> session cookie (token needs scope read)
    POST /ui/logout                 clears the cookie
    GET  /ui/api/entries            filtered list: q, project, agent, source, since, until, limit, offset
    GET  /ui/api/facets             projects, agents and sources with entry counts
    GET  /ui/api/stats              totals, per project and per day for the same filters
    POST /ui/api/entries/{id}       change an own entry: project, summary, details, tags, duration_min (write)
    POST /ui/api/entries/{id}/delete                                              (write)
    POST /ui/api/projects/rename    {"from", "to"}: rename, or merge into an existing project (write)
    GET  /ui/api/duplicates         groups of entries that were probably logged twice
    POST /ui/api/duplicates/merge   {"keep": id, "remove": [ids]}                 (write)
    POST /ui/api/duplicates/ignore  {"ids": [ids]}: "not a duplicate", never shown again (write)
    GET  /ui/api/export             same filters as /ui/api/entries, format=md|csv|json -> file download
    POST /ui/api/import             a JSON export (owner = the signed-in user); ?update=true overwrites (write)
    GET  /ui/api/tokens             own tokens (proxy sign-in)
    POST /ui/api/tokens             {"name", "scopes"} -> new own token, shown once (proxy sign-in)
    POST /ui/api/tokens/{name}/revoke  revoke an own token (proxy sign-in)

    GET  /api/entries, /api/facets, /api/stats   the same reads for scripts, bearer token only

(write) = proxy sign-in, or a token sign-in whose token has the write scope.

Every user sees only their own entries and tokens (strictly separated, no admin view).

Who may do what:
* Signed in through the reverse proxy (WORKLOG_PROXY_SECRET set, e.g. Traefik with
  Authelia forward auth): the user name comes from a header the proxy sets; this
  user reads their entries and manages their tokens. Without the shared secret in
  X-Worklog-Proxy the header is ignored, so nobody can fake it by talking to the
  container directly.
* Token sign-in: a signed session cookie (never the token itself) for the owner of
  the token, with the token's rights: read only, or with the write scope also
  corrections and imports. Token management stays with the proxy sign-in.
* Bearer token with scope read: reads, for the owner of the token.
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
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from .auth import (NAME_RE, SESSION_COOKIE, SESSION_TTL, check_session, check_token, create_session, create_token,
                   owner_of)
from .config import Settings
from .db import SCOPES, Database, as_dict
from .dupes import find_groups
from .report import read_export, stats, to_csv, to_json, to_markdown

log = logging.getLogger("worklog.webui")

PROXY_HEADER = "x-worklog-proxy"
IMPORT_MAX = 50 * 1024 * 1024  # a JSON export of ~100,000 entries
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
    owner: str          # whose data this request sees
    agent: str | None   # token name for session/bearer, None for proxy users
    via: str            # proxy | session | bearer
    manage_tokens: bool  # only with proxy sign-in
    can_edit: bool       # change entries, import: proxy sign-in, or a token with the write scope


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
                return Viewer(user, None, "proxy", manage_tokens=True, can_edit=True), None
            access, via = (check_session(db, request.cookies.get(SESSION_COOKIE)) if allow_cookie else None), "session"
        if access is None:
            return None, _json({"error": "not signed in"}, 401)
        if "read" not in access.scopes:
            return None, _json({"error": "token lacks the 'read' scope"}, 403)
        return Viewer(owner_of(access), access.client_id, via, manage_tokens=False,
                      can_edit="write" in access.scopes), None

    def manager(request: Request, change: bool, tokens: bool = False) -> tuple[Viewer | None, JSONResponse | None]:
        """Changes: entries need can_edit (proxy sign-in or a write token), tokens need the proxy sign-in."""
        who, err = viewer(request)
        if err:
            return None, err
        if tokens and not who.manage_tokens:
            return None, _json({"error": "only users signed in through the proxy may manage tokens"}, 403)
        if not who.can_edit:
            return None, _json({"error": "this sign-in is read only: use a token with the 'write' scope"}, 403)
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
        response = _json({"user": owner_of(access), "agent": access.client_id})
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
        return _json({"user": who.owner, "agent": who.agent, "via": who.via, "manage_tokens": who.manage_tokens,
                      "can_edit": who.can_edit,
                      "timezone": str(tz),
                      "logout_url": settings.logout_url if who.via == "proxy" else ""})

    # ------------------------------------------------------------------ data (read only)

    def reads(prefix: str, allow_cookie: bool) -> None:
        @mcp.custom_route(f"{prefix}/entries", methods=["GET"], include_in_schema=False)
        async def get_entries(request: Request) -> Response:
            who, err = viewer(request, allow_cookie)
            if err:
                return err
            try:
                f = filters(request)
                limit = int(request.query_params.get("limit", "50"))
                offset = int(request.query_params.get("offset", "0"))
            except ValueError as e:
                return _json({"error": str(e)}, 400)
            entries, total = db.query_entries(owner=who.owner, **f, limit=limit, offset=offset)
            return _json({"total": total, "offset": max(0, offset), "entries": [as_dict(e, tz) for e in entries]})

        @mcp.custom_route(f"{prefix}/facets", methods=["GET"], include_in_schema=False)
        async def get_facets(request: Request) -> Response:
            who, err = viewer(request, allow_cookie)
            return err or _json(db.facets(owner=who.owner))

        @mcp.custom_route(f"{prefix}/stats", methods=["GET"], include_in_schema=False)
        async def get_stats(request: Request) -> Response:
            who, err = viewer(request, allow_cookie)
            if err:
                return err
            try:
                f = filters(request)
            except ValueError as e:
                return _json({"error": str(e)}, 400)
            entries, _ = db.query_entries(owner=who.owner, **f, limit=None)
            return _json(stats(entries, tz))

    reads("/ui/api", allow_cookie=True)

    @mcp.custom_route("/ui/api/export", methods=["GET"], include_in_schema=False)
    async def export(request: Request) -> Response:
        who, err = viewer(request)
        if err:
            return err
        fmt = request.query_params.get("format", "md")
        if fmt not in ("md", "csv", "json"):
            return _json({"error": "format must be md, csv or json"}, 400)
        try:
            f = filters(request)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        entries, _ = db.query_entries(owner=who.owner, **f, limit=None)
        entries.reverse()  # oldest first, like `worklog export`
        span = "-".join(x.astimezone(tz).strftime("%Y%m%d") for x in (f["since"], f["until"]) if x) or "all"
        name = "-".join(filter(None, ["worklog", f["project"], span]))
        name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name) + "." + fmt
        if fmt == "json":
            body, media = to_json(entries, tz, db.instance_id(), f), "application/json; charset=utf-8"
        elif fmt == "csv":
            body, media = to_csv(entries, tz), "text/csv; charset=utf-8"
        else:
            body, media = to_markdown(entries, tz, title=f"Work log {who.owner}"), "text/markdown; charset=utf-8"
        return PlainTextResponse(body, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", **SECURITY_HEADERS})

    # ------------------------------------------------------------------ changes (write)

    async def json_object(request: Request) -> dict:
        body = json.loads(await request.body() or b"{}")
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        return body

    @mcp.custom_route("/ui/api/entries/{entry_id:int}", methods=["POST"], include_in_schema=False)
    async def change_entry(request: Request) -> Response:
        who, err = manager(request, change=True)
        if err:
            return err
        entry_id = request.path_params["entry_id"]
        try:
            body = await json_object(request)
            editable = {"project", "summary", "details", "tags", "duration_min"}
            if set(body) - editable:
                raise ValueError(f"cannot change: {', '.join(sorted(set(body) - editable))}")
            entry = db.update_entry(owner=who.owner, entry_id=entry_id, **body)
        except KeyError:
            return _json({"error": "unknown entry"}, 404)
        except (ValueError, TypeError) as e:
            return _json({"error": str(e)}, 400)
        log.info("entry_updated", extra={"entry_id": entry_id, "by": who.owner, "source": "web"})
        return _json(as_dict(entry, tz))

    @mcp.custom_route("/ui/api/entries/{entry_id:int}/delete", methods=["POST"], include_in_schema=False)
    async def remove_entry(request: Request) -> Response:
        who, err = manager(request, change=True)
        if err:
            return err
        entry_id = request.path_params["entry_id"]
        try:
            db.delete_entry(owner=who.owner, entry_id=entry_id)
        except KeyError:
            return _json({"error": "unknown entry"}, 404)
        log.info("entry_deleted", extra={"entry_id": entry_id, "by": who.owner, "source": "web"})
        return _json({"deleted": entry_id})

    @mcp.custom_route("/ui/api/import", methods=["POST"], include_in_schema=False)
    async def import_json(request: Request) -> Response:
        who, err = manager(request, change=True)
        if err:
            return err
        body = await request.body()
        if len(body) > IMPORT_MAX:
            return _json({"error": f"file larger than {IMPORT_MAX // 1024 // 1024} MB"}, 413)
        try:
            items = read_export(json.loads(body or b"null"))
        except ValueError as e:  # includes JSONDecodeError
            return _json({"error": str(e)}, 400)
        update = request.query_params.get("update", "").lower() in ("1", "true", "yes")
        result = db.import_export(who.owner, items, update=update)
        log.info("json_import", extra={"by": who.owner, "status": {
            k: (len(v) if isinstance(v, list) else v) for k, v in result.items()}})
        return _json({**result, "errors": result["errors"][:50]})

    @mcp.custom_route("/ui/api/duplicates", methods=["GET"], include_in_schema=False)
    async def duplicates(request: Request) -> Response:
        who, err = viewer(request)
        if err:
            return err
        entries, _ = db.query_entries(owner=who.owner, limit=None)
        groups = find_groups(entries, db.ignored_duplicates(owner=who.owner))
        return _json({"groups": [{"score": g["score"], "entries": [as_dict(e, tz) for e in g["entries"]]}
                                 for g in groups]})

    @mcp.custom_route("/ui/api/duplicates/merge", methods=["POST"], include_in_schema=False)
    async def merge_duplicates(request: Request) -> Response:
        who, err = manager(request, change=True)
        if err:
            return err
        try:
            body = await json_object(request)
            keep, remove = body.get("keep"), body.get("remove")
            if not isinstance(keep, int) or not isinstance(remove, list) or not all(isinstance(i, int) for i in remove):
                raise ValueError("expected {\"keep\": id, \"remove\": [ids]}")
            entry = db.merge_entries(owner=who.owner, keep_id=keep, remove_ids=remove)
        except KeyError:
            return _json({"error": "unknown entry"}, 404)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        log.info("entries_merged", extra={"entry_id": entry.id, "by": who.owner, "status": {"removed": len(remove)}})
        return _json(as_dict(entry, tz))

    @mcp.custom_route("/ui/api/duplicates/ignore", methods=["POST"], include_in_schema=False)
    async def ignore_duplicates(request: Request) -> Response:
        who, err = manager(request, change=True)
        if err:
            return err
        try:
            body = await json_object(request)
            ids = body.get("ids")
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
                raise ValueError("expected {\"ids\": [ids]}")
            db.ignore_duplicates(owner=who.owner, ids=ids)
        except KeyError:
            return _json({"error": "unknown entry"}, 404)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        return _json({"ignored": sorted(set(ids))})

    @mcp.custom_route("/ui/api/projects/rename", methods=["POST"], include_in_schema=False)
    async def rename_project(request: Request) -> Response:
        who, err = manager(request, change=True)
        if err:
            return err
        try:
            body = await json_object(request)
            old, new = str(body.get("from") or ""), str(body.get("to") or "").strip()
            merged = any(p["project"] == new for p in db.list_projects(owner=who.owner))
            moved = db.rename_project(owner=who.owner, old=old, new=new)
        except KeyError:
            return _json({"error": "unknown project"}, 404)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        log.info("project_renamed", extra={"by": who.owner, "status": {"entries": moved, "merged": merged}})
        return _json({"from": old, "to": new, "entries": moved, "merged": merged})
    reads("/api", allow_cookie=False)   # scripts: bearer only, so cookies never reach /api

    # ------------------------------------------------------------------ own tokens (proxy sign-in)

    def token_dict(r) -> dict:
        return {"name": r.name, "scopes": r.scopes, "created_at": _iso(r.created_at, tz),
                "last_used_at": _iso(r.last_used_at, tz), "revoked_at": _iso(r.revoked_at, tz)}

    @mcp.custom_route("/ui/api/tokens", methods=["GET"], include_in_schema=False)
    async def list_tokens(request: Request) -> Response:
        who, err = manager(request, change=False, tokens=True)
        return err or _json({"scopes": list(SCOPES), "tokens": [token_dict(r) for r in db.list_tokens(owner=who.owner)]})

    @mcp.custom_route("/ui/api/tokens", methods=["POST"], include_in_schema=False)
    async def new_token(request: Request) -> Response:
        who, err = manager(request, change=True, tokens=True)
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
            token = create_token(db, who.owner, name, scopes)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        log.info("token_created", extra={"agent": name, "by": who.owner, "scopes": ",".join(sorted(set(scopes)))})
        return _json({"token": token, **token_dict(db.get_token(who.owner, name))}, 201)

    @mcp.custom_route("/ui/api/tokens/{name}/revoke", methods=["POST"], include_in_schema=False)
    async def revoke_token(request: Request) -> Response:
        who, err = manager(request, change=True, tokens=True)
        if err:
            return err
        name = request.path_params["name"]
        if not NAME_RE.fullmatch(name):
            return _json({"error": "unknown token"}, 404)
        try:
            record = db.revoke_token(who.owner, name)
        except KeyError:
            return _json({"error": "unknown token"}, 404)
        except ValueError as e:
            return _json({"error": str(e)}, 409)
        log.info("token_revoked", extra={"agent": name, "by": who.owner})
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
