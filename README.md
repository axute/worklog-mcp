# worklog

An MCP server that collects work log entries from AI agents (Claude Code, Docker Agent, claude.ai and other MCP clients) and makes them searchable again.

- One endpoint, Streamable HTTP, stateless: `https://<your-host>/mcp`
- One bearer token per agent; the server takes the agent name from the token, so entries cannot be written under another name
- SQLite storage (WAL), tokens stored only as SHA-256 hashes
- Times stored in UTC, shown in the configured time zone

> Status: server, tools, REST API, CLI, container setup, importer and web view are done.

## Tools

| Tool | Parameters | Returns |
|---|---|---|
| `log_entry` | `project`, `summary` (max. 300 chars), optional `details`, `tags`, `duration_min`, `session_id` | id and timestamp |
| `list_entries` | optional `project`, `agent`, `since`, `until`, `limit` (default 20, max. 500) | entries, newest first |
| `search_entries` | `query`, optional `project`, `limit` | full text matches (summary, details, project, tags), best first; every word must match, word beginnings are enough |
| `summarize_period` | `since`, `until`, optional `project` | entries grouped by day and project with durations and totals, e.g. for weekly reports |
| `list_projects` | – | projects with entry count, logged minutes and last entry |

Dates without a time zone (`2026-11-13`) are read in the server's time zone.

Scopes: `write` is needed for `log_entry`, `read` for all other tools.

## Web view

`https://<your-host>/ui/` shows the log in the browser:

- **Entries:** newest first, grouped by day, with full text search and filters for project, agent, source and period. Click an entry for details and tags, click a project to filter by it. Filters are kept in the URL, so a view can be bookmarked.
- **Overview:** totals, time or entries per project, and a chart per day, week or month (chosen by the length of the period), also available as a table.
- **Tokens** (only after signing in through the proxy, see below): create tokens, see when each was last used, revoke them. A new token is shown once.

The page loads nothing from other hosts and follows the light or dark theme and the language (English or German) of the browser.

### Signing in through a reverse proxy (recommended)

Put `/ui` behind the login you already use for other services, e.g. Traefik forward auth with Authelia, Authentik or oauth2-proxy. The proxy sets the user name in a header (`Remote-User` by default) and a shared secret in `X-Worklog-Proxy`; worklog trusts the user header only when the secret matches, so nobody can fake it by talking to the container directly. Users signed in this way may read everything and manage tokens.

```bash
# .env: a random secret, at least 24 characters
WORKLOG_PROXY_SECRET=$(openssl rand -base64 32)
```

`compose.example.yaml` contains the Traefik routers for this (commented out): a second router for `/` and `/ui` with the auth middleware followed by one that adds the secret, and a middleware on the main router that removes both headers from requests to `/mcp` and `/api`. Token changes are accepted only from the page itself (same origin, JSON body).

### Signing in with a token

Without a proxy login, sign in on the page with any token that has the `read` scope; a dedicated one is a good idea:

```bash
docker exec -it worklog-mcp worklog token create web --scopes read
```

The browser gets a signed session cookie (HttpOnly, SameSite=Strict, Secure behind HTTPS, 30 days). It never contains the token, gives read access only (no token management) and stops working when the token is revoked.

### JSON for scripts

`GET /api/entries` (`q`, `project`, `agent`, `source`, `since`, `until`, `limit`, `offset`), `GET /api/facets` and `GET /api/stats` with a bearer token that has the `read` scope. The page itself uses the same endpoints below `/ui/api/`.

## REST API

For hooks and scripts that don't speak MCP. Same tokens, scope `write`.

```bash
TOKEN=wl_...
URL=https://worklog.example.com

# one entry (source "hook")
curl -sS -X POST "$URL/api/entries" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"project": "website", "summary": "Deployed the new start page", "tags": ["deploy"], "duration_min": 20}'
# -> 201 {"id": 1, "ts": "2026-11-13T10:42:00+01:00"}

# bulk import (source "import"), max. 500 per request; external_id prevents duplicates
curl -sS -X POST "$URL/api/entries/bulk" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"entries": [{"external_id": "claude-code:6f1c", "ts": "2026-10-02T14:05:00Z",
                    "project": "website", "summary": "Set up the build pipeline"}]}'
# -> {"created": 1, "updated": 0, "skipped": 0, "errors": []}
# the same request again -> {"created": 0, ..., "skipped": 1}; add ?update=true to overwrite
```

Errors come back as `{"error": "..."}` with 400, 401 (missing or invalid token), 403 (missing scope), 413 or 415.

## Tokens

```bash
worklog token create claude-code-laptop --scopes write,read   # prints the token once
worklog token list
worklog token revoke claude-code-laptop
```

Signed in through the proxy, the web view does the same under **Tokens**. Revoked names cannot be reused, so entries keep a unique agent name.

Tokens look like `wl_…` (32 random bytes). Store them in a password manager or the client configuration, never in prompts or agent instructions.

## Export and backup

```bash
worklog export --since 2026-11-09 --until 2026-11-16              # Markdown, grouped by day and project
worklog export --format csv --project website -o website.csv
worklog backup --keep 14   # consistent copy to backups/ next to the database, keeps the newest 14
```

## Deployment (Docker + Traefik)

1. Create a data directory on a real file system, e.g. directly on a pool or disk. SQLite file locks are unreliable on FUSE/union mounts (on Unraid: use `/mnt/cache/...` or `/mnt/diskN/...`, not `/mnt/user/...`). On Unraid give it to `nobody:users`: `chown 99:100 <dir>`.
2. `cp compose.example.yaml compose.yaml` and `cp .env.example .env`, then set `WORKLOG_HOST`, `TRAEFIK_NETWORK`, `WORKLOG_DATA` (and `WORKLOG_SRC` if the stack file lives outside the repository). Both files are ignored by git.
3. `docker compose up -d --build`, then check `docker inspect --format '{{.State.Health.Status}}' worklog-mcp` → `healthy`.
4. Create a token per agent: `docker exec -it worklog-mcp worklog token create <agent-name>`.
5. `curl -i https://<host>/mcp` without a token must answer `401`.

The container publishes no port; it is reachable only through Traefik on the given network. The image runs as `99:100`. Don't put a `compress` middleware on the router: it can buffer streamed MCP responses.

Nightly backup, e.g. via cron or the Unraid User Scripts plugin: `docker exec worklog-mcp worklog backup --keep 14` (writes to `backups/` in the data directory). To keep backups elsewhere, mount a second volume (see `compose.example.yaml`) and use `--dir /backups`; the directory must be writable for UID 99 / GID 100.

## Importing earlier sessions

`worklog-import` turns past Claude Code sessions and claude.ai conversations into entries, with their original time stamps. It runs where the data lives (usually your workstation), needs only Python 3.9+ and no extra packages, and uploads finished summaries only – never raw transcripts.

```bash
# from a checkout, without installing:
export PYTHONPATH=/path/to/worklog-mcp/src
cp import-rules.example.yaml import-rules.yaml   # map folders/keywords to projects

# 1. extract + summarize -> import-preview.jsonl and import-preview.md
python -m worklog_import prepare --rules import-rules.yaml --since 2026-10-01 --limit 10
python -m worklog_import prepare --source claude-ai --path ~/Downloads/data-export.zip --rules import-rules.yaml

# 2. read import-preview.md; delete lines in import-preview.jsonl or set "skip": true

# 3. upload (token with scope write)
export WORKLOG_URL=https://worklog.example.com WORKLOG_TOKEN=wl_...
python -m worklog_import upload            # --update overwrites already imported entries
```

- **Sources:** Claude Code transcripts in `~/.claude/projects/*/*.jsonl` (subagent transcripts are ignored, sessions active in the last 2 hours are skipped), and the claude.ai data export (*Settings → Privacy → Export data*; ZIP, folder or `conversations.json`).
- **Summaries:** by default with `claude -p`, isolated from your setup (no tools, no MCP servers, no hooks, no session file), so it can never write to the log itself; one entry per result, up to 8 per session, private or trivial conversations are marked as skipped. `--summarizer none` makes one entry per session from the first prompt, without AI.
- **Secrets:** tokens, keys, passwords, private keys, JWTs, credentials in URLs and long random strings are replaced with `[REDACTED:…]` before anything is summarized or uploaded. Check the preview anyway.
- **Repeatable:** summaries are cached in `import-cache.json`; entries carry `external_id` `claude-code:<session>:<n>` / `claude-ai:<uuid>:<n>`, so running the import again only adds new sessions.
- Claude Code deletes local transcripts after 30 days by default. Set `"cleanupPeriodDays": 3650` in `~/.claude/settings.json` to keep them.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WORKLOG_DB` | `/data/worklog.db` | SQLite file |
| `WORKLOG_HOST` | `0.0.0.0` | listen address |
| `WORKLOG_PORT` | `8000` | listen port |
| `WORKLOG_TZ` / `TZ` | `Europe/Berlin` | display time zone |
| `WORKLOG_PROXY_SECRET` | – | enables sign-in through a reverse proxy; the proxy sends it in `X-Worklog-Proxy` (min. 24 characters) |
| `WORKLOG_USER_HEADER` | `Remote-User` | header with the user name set by the proxy |
| `WORKLOG_WEB_USERS` | – | optional comma-separated list of users allowed in; empty: everyone the proxy lets through |
| `WORKLOG_LOGOUT_URL` | – | sign-out link shown to proxy users, e.g. `https://auth.example.com/logout` |

Run: `python -m worklog.server`. `GET /healthz` answers `ok` without a token (for health checks); everything under `/mcp` needs `Authorization: Bearer <token>`.

## Development

```bash
./scripts/test.sh          # pytest in a python:3.12-slim container, dependencies cached in a Docker volume
./scripts/test.sh -k auth  # extra pytest arguments are passed through
```

The tests start the real server on `127.0.0.1` with a temporary database and talk to it with the FastMCP client; no outside network is used.

## License

MIT, see [LICENSE](LICENSE).
