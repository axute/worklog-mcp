"""Plain REST endpoints for hooks and the importer (no MCP needed).

    POST /api/entries        one entry, source "hook"        (scope write)
    POST /api/entries/bulk   up to 500 entries with external_id, source "import"
                             (scope write); ?update=true overwrites known ids
    GET  /healthz            "ok", no token

FastMCP does not protect custom routes, so every handler checks the token itself.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from .auth import check_token
from .config import Settings
from .db import BULK_MAX, Database

log = logging.getLogger("worklog.api")

MAX_BODY = 4 * 1024 * 1024  # 4 MB, enough for 500 entries with details


def _error(status: int, message: str, **headers: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers=headers or None)


def _authorize(db: Database, request: Request, scope: str) -> tuple[str | None, JSONResponse | None]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    access = check_token(db, token.strip()) if scheme.lower() == "bearer" else None
    if access is None:
        return None, _error(401, "missing or invalid bearer token", **{"WWW-Authenticate": 'Bearer realm="worklog"'})
    if scope not in access.scopes:
        return None, _error(403, f"token lacks the '{scope}' scope")
    return access.client_id, None


async def _json_body(request: Request) -> tuple[Any, JSONResponse | None]:
    if "application/json" not in request.headers.get("content-type", ""):
        return None, _error(415, "Content-Type must be application/json")
    body = await request.body()
    if len(body) > MAX_BODY:
        return None, _error(413, f"body larger than {MAX_BODY // 1024 // 1024} MB")
    try:
        return json.loads(body), None
    except ValueError:
        return None, _error(400, "body is not valid JSON")


def register_api(mcp: FastMCP, db: Database, settings: Settings) -> None:
    tz = settings.timezone

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    @mcp.custom_route("/api/entries", methods=["POST"], include_in_schema=False)
    async def post_entry(request: Request) -> JSONResponse:
        agent, err = _authorize(db, request, "write")
        if err:
            return err
        data, err = await _json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return _error(400, "body must be a JSON object")
        unknown = set(data) - {"project", "summary", "details", "tags", "duration_min", "session_id"}
        if unknown:
            return _error(400, f"unknown fields: {', '.join(sorted(unknown))}")
        tags = data.get("tags")
        duration = data.get("duration_min")
        if tags is not None and not isinstance(tags, list):
            return _error(400, "tags must be a list")
        if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool)):
            return _error(400, "duration_min must be an integer")
        try:
            entry = db.add_entry(
                agent=agent, source="hook", project=str(data.get("project") or ""),
                summary=str(data.get("summary") or ""), details=data.get("details"),
                tags=tags, duration_min=duration, session_id=data.get("session_id"),
            )
        except ValueError as e:
            return _error(400, str(e))
        log.info("entry_created", extra={"entry_id": entry.id, "agent": agent, "source": "hook"})
        return JSONResponse({"id": entry.id, "ts": entry.ts.astimezone(tz).isoformat()}, status_code=201)

    @mcp.custom_route("/api/entries/bulk", methods=["POST"], include_in_schema=False)
    async def post_bulk(request: Request) -> JSONResponse:
        agent, err = _authorize(db, request, "write")
        if err:
            return err
        data, err = await _json_body(request)
        if err:
            return err
        items = data.get("entries") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return _error(400, "body must be a list of entries or {\"entries\": [...]}")
        if len(items) > BULK_MAX:
            return _error(413, f"at most {BULK_MAX} entries per request")
        update = request.query_params.get("update", "").lower() in ("1", "true", "yes")
        result = db.import_entries(agent, items, update=update)
        log.info("bulk_import", extra={"agent": agent, "source": "import", "status": {
            k: (len(v) if isinstance(v, list) else v) for k, v in result.items()}})
        return JSONResponse(result, status_code=200)
