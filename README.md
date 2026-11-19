# worklog

An MCP server that collects work log entries from AI agents (Claude Code, Docker Agent, claude.ai and other MCP clients) and makes them searchable again.

- One endpoint, Streamable HTTP, stateless: `https://<your-host>/mcp`; or stdio, started by the client itself (`docker run -i …`)
- One bearer token per agent; the server takes the agent name from the token, so entries cannot be written under another name
- SQLite storage (WAL), tokens stored only as SHA-256 hashes
- Times stored in UTC, shown in the configured time zone
- Several users, strictly separated; optional sign-in through your reverse proxy (e.g. Authelia)
- Web view with search, overview charts and token management

## Quick start

Run the server on your machine and connect Claude Code to it. For a server that other devices or claude.ai reach over HTTPS, continue with the [setup guide](#setup-guide).

**With Docker** (image for amd64 and arm64 from GitHub Container Registry):

```bash
mkdir -p ~/worklog-data && sudo chown 99:100 ~/worklog-data      # container runs as UID 99; skip on Docker Desktop
docker run -d --name worklog-mcp --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v ~/worklog-data:/data ghcr.io/axute/worklog-mcp:latest

docker exec worklog-mcp worklog token create laptop --owner me   # prints the token (wl_...) once
```

**Without Docker** (Python 3.12 or newer):

```bash
python3 -m venv ~/.worklog && . ~/.worklog/bin/activate
pip install git+https://github.com/axute/worklog-mcp.git           # needs git

export WORKLOG_DB=~/worklog/worklog.db WORKLOG_HOST=127.0.0.1    # default: /data/worklog.db on 0.0.0.0
worklog-server                                                    # runs in the foreground; Ctrl+C stops it

# in a second terminal, with the same WORKLOG_DB:
worklog token create laptop --owner me                            # prints the token (wl_...) once
```

**Connect Claude Code** and check:

```bash
claude mcp add --transport http --scope user worklog http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer wl_..."
claude mcp list                                                   # worklog … ✓ Connected
```

In a new Claude Code session, ask for example *"Log to worklog: project test, summary 'worklog is running'"*. The entry shows up in the web view at <http://127.0.0.1:8000/ui/> (sign in there with a token that has the `read` scope; `laptop` has it), or with `worklog export --owner me`. To make agents log on their own, add the [agent rule](integrations/agent-rule.md) to `~/.claude/CLAUDE.md`.

### Let the client start it (stdio, `docker run -i`)

Instead of a server that runs all the time, the MCP client can start worklog itself for each session and talk to it over stdin/stdout. There is no token then: user and agent name come from `WORKLOG_OWNER` and `WORKLOG_AGENT` in the client configuration, and only programs on this machine can reach it.

**Claude Desktop** (`claude_desktop_config.json`), and the same block in other clients with an `mcpServers` file (Cursor, Windsurf, …):

```json
{
  "mcpServers": {
    "worklog": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-v", "worklog-data:/data",
               "-e", "WORKLOG_OWNER=me", "-e", "WORKLOG_AGENT=claude-desktop",
               "ghcr.io/axute/worklog-mcp:latest", "worklog-server", "--stdio"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add --scope user worklog -- docker run -i --rm -v worklog-data:/data \
  -e WORKLOG_OWNER=me -e WORKLOG_AGENT=claude-code ghcr.io/axute/worklog-mcp:latest worklog-server --stdio
```

**Without Docker**, after the `pip install` above: `"command": "worklog-server", "args": ["--stdio"]` with `"env": {"WORKLOG_OWNER": "me", "WORKLOG_AGENT": "claude-desktop", "WORKLOG_DB": "/home/me/worklog/worklog.db"}`.

- `-i` is required (stdin stays open), `--rm` removes the container when the session ends; don't add `-t`.
- `worklog-data` is a named Docker volume, created on first use. Prefer it over a bind mount on Docker Desktop (Mac, Windows), where SQLite file locks on host folders are unreliable.
- Several clients may run at the same time on the same volume, and an HTTP server (above, with `-v worklog-data:/data` instead of `~/worklog-data`) can share it too, so its web view shows the stdio entries. Use one agent name per client.
- Docker pulls the image at the first start, which can take a moment; pin a version tag once you rely on it. Logs go to stderr, so the client's MCP log shows them.

## Setup guide

From an empty server to agents that log on their own, reachable over HTTPS. You need a host with Docker and Docker Compose, and a way to reach the server over HTTPS.

**Who connects from where** decides how reachable the server must be:

| Client | Connects from | Needs |
|---|---|---|
| Claude Code, IDE agents, scripts | your machine | reachable from your machine (LAN/VPN is fine) |
| claude.ai, Claude Desktop connectors, Claude mobile | Anthropic's cloud | a **public** HTTPS URL (e.g. via a tunnel); the bearer token protects it |

### 1. Get the code and a data directory

The compose files are in the repository; the image itself can come from `ghcr.io/axute/worklog-mcp` (see below) or be built from the checkout.

```bash
git clone https://github.com/axute/worklog-mcp.git
cd worklog-mcp
mkdir -p /srv/worklog-mcp && chown 99:100 /srv/worklog-mcp   # the container runs as UID 99 / GID 100
```

Put the data directory on a real file system: SQLite file locks are unreliable on FUSE/union mounts (on Unraid use `/mnt/cache/...` or `/mnt/diskN/...`, not `/mnt/user/...`).

### 2. Configure

**Behind Traefik** (recommended):

```bash
cp compose.example.yaml compose.yaml
cp .env.example .env      # set WORKLOG_HOST, TRAEFIK_NETWORK, WORKLOG_DATA, WORKLOG_TZ
```

Both files are ignored by git. `compose.yaml` uses the published image `ghcr.io/axute/worklog-mcp:latest`; to build from the checkout instead, swap in the commented `build:` lines. If Traefik has no default entrypoint with a certificate resolver, uncomment the `entrypoints` and `tls.certresolver` labels in `compose.yaml`. Don't add a `compress` middleware: it can buffer streamed MCP responses.

**Behind another reverse proxy** (nginx, Caddy, …) or for a first try on one machine, publish the port instead of the Traefik labels:

```yaml
# compose.yaml
services:
  worklog:
    image: ghcr.io/axute/worklog-mcp:latest
    container_name: worklog-mcp
    restart: unless-stopped
    environment:
      TZ: Europe/Berlin
    volumes:
      - /srv/worklog-mcp:/data
    ports:
      - "127.0.0.1:8000:8000"   # only this machine; let your HTTPS proxy forward to it
```

Plain HTTP is fine for `localhost`. Anything that leaves the machine must go through HTTPS, because the token travels in a header. The proxy must not buffer responses for `/mcp`.

### 3. Start and check

```bash
docker compose up -d
docker inspect --format '{{.State.Health.Status}}' worklog-mcp     # -> healthy (after ~10 s)
curl -i https://worklog.example.com/healthz                         # -> 200 ok
curl -i -X POST https://worklog.example.com/mcp                     # -> 401: no token, as it should be
```

### 4. Create a token

One token per agent or device; its name becomes the `agent` of every entry written with it. `--owner` is your user name (with proxy sign-in, use the name your proxy reports, so the web view shows the same data):

```bash
docker exec -it worklog-mcp worklog token create claude-code-laptop --owner alice --scopes write,read
```

The token (`wl_…`) is shown once. Keep it in a password manager or the client configuration, never in prompts or agent instructions. With proxy sign-in (see [Web view](#web-view)) you can do this in the browser under **Tokens** instead.

### 5. Connect your clients

The URL always ends in **`/mcp`**, and the token goes into an `Authorization: Bearer …` header. The server deliberately offers no OAuth: a client that asks you to sign in has not sent the header.

**Claude Code**

```bash
claude mcp add --transport http --scope user worklog https://worklog.example.com/mcp \
  --header "Authorization: Bearer wl_..."
claude mcp list        # worklog … ✓ Connected
```

`--scope user` makes it available in all projects. To change it later: `claude mcp remove worklog -s user` and add it again. The same entry in `~/.claude.json` under `mcpServers`:

```json
"worklog": {
  "type": "http",
  "url": "https://worklog.example.com/mcp",
  "headers": { "Authorization": "Bearer wl_..." }
}
```

**claude.ai and Claude Desktop** (*Settings → Connectors → Add custom connector*)

- URL: `https://worklog.example.com/mcp`, which must be reachable from the internet.
- Authentication: **No sign-in**.
- Request header: name `authorization`, value `Bearer wl_...` (including `Bearer` and the space).

If your plan shows no field for request headers, the connector can only do OAuth, which this server does not offer. Connectors added in claude.ai also show up in Claude Code sessions of the same account (as `mcp__claude_ai_worklog__…`), so you may not need a separate Claude Code entry.

**Other MCP clients** (VS Code, JetBrains, Cursor, Docker Agent, …) work if they support *Streamable HTTP* with custom headers. They need the same three things: type `http`, the URL with `/mcp`, and the `Authorization` header. VS Code, for example, in `.vscode/mcp.json` or the user settings, asks for the token once instead of storing it in the file:

```json
{
  "inputs": [{ "type": "promptString", "id": "worklog-token", "description": "worklog token", "password": true }],
  "servers": {
    "worklog": {
      "type": "http",
      "url": "https://worklog.example.com/mcp",
      "headers": { "Authorization": "Bearer ${input:worklog-token}" }
    }
  }
}
```

Scripts and hooks that don't speak MCP use the [REST API](#rest-api) with the same token.

### 6. Tell the agent when to log

Agents log reliably only with an explicit rule in the instructions they load in every session. Copy the block from [`integrations/agent-rule.md`](integrations/agent-rule.md) (English or German) into `~/.claude/CLAUDE.md` for Claude Code, into the personal preferences or project instructions in claude.ai, or into the global instructions of your other agents.

### 7. Optional: Claude Code reminder hook

[`integrations/claude-code/worklog-stop.py`](integrations/claude-code/worklog-stop.py) is a *Stop* hook. When Claude finishes a turn in which it changed something (file edits, writing shell commands such as `git commit`, `docker compose up` or `rm`) and has not called `log_entry` afterwards, the hook sends it back once with a reminder. It never writes to the log itself, never reminds twice in a row, and does nothing if it fails. It needs Python 3 on the machine.

```bash
mkdir -p ~/.claude/hooks
cp integrations/claude-code/worklog-stop.py ~/.claude/hooks/
```

Add this to `~/.claude/settings.json`, merged with what is already there:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python3 ~/.claude/hooks/worklog-stop.py", "timeout": 10 } ] }
    ]
  }
}
```

The hook recognizes any MCP tool whose name contains `worklog` and ends in `log_entry`, so it works with both `claude mcp add` and a claude.ai connector.

### 8. Try it

Start a **new** session (MCP servers and instructions are loaded at start), let the agent finish something small, and check that an entry arrives: in the web view at `https://worklog.example.com/ui/`, or with

```bash
docker exec worklog-mcp worklog export --owner alice --since 2026-01-01 | tail
```

### 9. Backups and updates

- **Backup**, e.g. nightly via cron or the Unraid User Scripts plugin: `docker exec worklog-mcp worklog backup --keep 14`. This writes a consistent copy to `backups/` in the data directory and keeps the newest 14. To keep backups elsewhere, mount a second volume (see `compose.example.yaml`) and add `--dir /backups`; that directory must be writable for UID 99 / GID 100.
- **Update:** with the published image `docker compose pull && docker compose up -d`; when building from the checkout `git pull`, then `docker compose build && docker compose up -d --force-recreate`. A plain restart keeps running the old image. Database migrations run on start.
- **Images:** `ghcr.io/axute/worklog-mcp` is built by GitHub Actions after the tests pass: `:latest` follows `main`, `:sha-<commit>` pins a build, release tags `v1.2.3` also publish `:1.2.3`, `:1.2` and `:1`.
- **Older installations** without user support: see [Users](#users) before updating.

### Optional next steps

- [Web view with sign-in through your reverse proxy](#signing-in-through-a-reverse-proxy-recommended), including token management in the browser and more users.
- [Import earlier sessions](#importing-earlier-sessions) from Claude Code, claude.ai or JetBrains Junie.

## Tools

| Tool | Parameters | Returns |
|---|---|---|
| `log_entry` | `project`, `summary` (max. 300 chars), optional `details`, `tags`, `duration_min`, `session_id`, `ts` | id and timestamp; `possible_duplicate_of` with a hint if a very similar entry exists close to that time |
| `log_entries` | `entries`: up to 100 objects with the fields of `log_entry` | ids per entry; all are checked before any is written |
| `update_entry` | `entry_id`, and any of `project`, `summary`, `details`, `tags`, `duration_min` | the corrected entry; only entries of the same agent (token) |
| `list_entries` | optional `project`, `agent`, `since`, `until`, `limit` (default 20, max. 500) | entries, newest first |
| `search_entries` | `query`, optional `project`, `limit` | full text matches (summary, details, project, tags), best first; every word must match, word beginnings are enough |
| `summarize_period` | `since`, `until`, optional `project` | entries grouped by day and project with durations and totals, e.g. for weekly reports |
| `list_projects` | – | projects with entry count, logged minutes and last entry |

Dates without a time zone (`2026-11-13`) are read in the server's time zone.

Scopes: `write` is needed for `log_entry`, `log_entries` and `update_entry`, `read` for all other tools.

**Logging afterwards:** `ts` is when the work was done – an ISO date (`2025-03-14`, counted as noon) or datetime, in the server's time zone unless it has an offset; never in the future. The time of logging is kept as well (`created_at`), and the web view marks such entries as *logged afterwards*.

**Prompts** (shown as ready-made commands in Claude Desktop, Claude Code and other clients):

| Prompt | Arguments | Does |
|---|---|---|
| `weekly_report` | `weeks_ago` (0 = this week, default 1 = last week), optional `project`, `language` | asks the model to call `summarize_period` for that calendar week (Monday to Sunday) and write a short report from it |
| `monthly_report` | `months_ago` (default 1), optional `project`, `language` | the same for a calendar month |
| `backfill_my_commits` | optional `since`, `language` | the same for the project that is open, from your own commits only: finds the project name (and an existing one in the log), asks which git authors are you, shows a preview and waits for your OK before writing |
| `backfill_project` | `project`, optional `source` (e.g. a repository path), `since`, `language` | logs the history of an earlier project afterwards: checks what is logged already, reads the git history, changelog and docs, groups them into results and writes them with `log_entries` and the real dates (tagged `backfill`; nothing invented, durations only where the sources show them) |

## Web view

`https://<your-host>/ui/` shows the log in the browser:

- **Entries:** newest first, grouped by day, with full text search and filters for project, agent, source and period. Click an entry for details and tags, click a project to filter by it. Filters are kept in the URL, so a view can be bookmarked.
- **Overview:** totals, time or entries per project, and a chart per day, week or month (chosen by the length of the period), also available as a table.
- **Export:** the current filter as Markdown, CSV or JSON download (links above the list).
- **Import** (needs write rights, see below): a JSON export, e.g. from another worklog instance, becomes part of your log. See [JSON export and import](#json-export-and-import).
- **Duplicates:** entries that look like the same work logged twice (same project, at most 3 hours apart, nearly the same summary; summaries with different numbers never count) are shown above the list. Pick the one to keep and merge: details and tags are combined, the longest duration counts once, the earliest time stays, the others are deleted. *Not a duplicate* hides the group for good. Both need write rights.
- **Corrections** (need write rights): edit or delete an entry (open it, then *Edit* / *Delete*); with a project filter set, *Rename project …* renames it, or merges it into an existing project.
- **Tokens** (only after signing in through the proxy): create your tokens, see when each was last used, revoke them. A new token is shown once.

The page loads nothing from other hosts and follows the light or dark theme and the language (English or German) of the browser.

### Signing in through a reverse proxy (recommended)

Put `/ui` behind the login you already use for other services, e.g. Traefik forward auth with Authelia, Authentik or oauth2-proxy. The proxy sets the user name in a header (`Remote-User` by default) and a shared secret in `X-Worklog-Proxy`; worklog trusts the user header only when the secret matches, so nobody can fake it by talking to the container directly. Users signed in this way read their own entries and manage their own tokens.

```bash
# .env: a random secret, at least 24 characters
WORKLOG_PROXY_SECRET=$(openssl rand -base64 32)
```

`compose.example.yaml` contains the Traefik routers for this (commented out): a second router for `/` and `/ui` with the auth middleware followed by one that adds the secret, and a middleware on the main router that removes both headers from requests to `/mcp` and `/api`. Token changes are accepted only from the page itself (same origin, JSON body).

### Signing in with a token

Without a proxy login, sign in on the page with a token that has the `read` scope; a dedicated one is a good idea:

```bash
docker exec -it worklog-mcp worklog token create web --owner <user> --scopes read         # look only
docker exec -it worklog-mcp worklog token create web --owner <user> --scopes read,write   # also edit and import
```

The browser gets a signed session cookie (HttpOnly, SameSite=Strict, Secure behind HTTPS, 30 days). It never contains the token and stops working when the token is revoked.

**Rights in the web view:** signed in through the proxy, you may do everything with your own data. Signed in with a token, you get that token's rights: with `read` you can read and export; with `read` and `write` you can also edit, delete, rename, merge duplicates and import. Managing tokens always needs the proxy sign-in, so an agent's token cannot create more tokens. Changes are accepted only from the page itself (same origin, JSON body).

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
# -> 201 {"id": 1, "ts": "2026-11-13T10:42:00+01:00"}; add "ts": "2025-03-14T16:30" for work done earlier

# bulk import (source "import"), max. 500 per request; external_id prevents duplicates
curl -sS -X POST "$URL/api/entries/bulk" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"entries": [{"external_id": "claude-code:6f1c", "ts": "2026-10-02T14:05:00Z",
                    "project": "website", "summary": "Set up the build pipeline"}]}'
# -> {"created": 1, "updated": 0, "skipped": 0, "errors": []}
# the same request again -> {"created": 0, ..., "skipped": 1}; add ?update=true to overwrite
```

Errors come back as `{"error": "..."}` with 400, 401 (missing or invalid token), 403 (missing scope), 413 or 415.

## Users

Every entry and token belongs to a user, and users are strictly separated: a user sees only their own entries, projects and tokens, through every way in (MCP tools, REST API, web view, export). There is no admin view of other users' entries.

- With sign-in through the proxy, the user is the name the proxy reports (e.g. the Authelia user name). A new user simply starts with an empty log and creates tokens in the web view.
- A token belongs to the user who created it; everything written with it belongs to that user, and reading with it shows only that user's entries.
- Token names (= agent names) are unique per user, so two users can both have a `laptop` token. Import ids (`external_id`) are also per user.

**Upgrading from a version without users:** existing entries and tokens need an owner. Set `WORKLOG_LEGACY_OWNER=<user name>` (e.g. your Authelia user) for the first start after the upgrade; the server refuses to start without it as long as there is old data. Existing tokens keep working and now belong to that user. Take a backup first (`worklog backup`).

## Tokens

```bash
worklog token create claude-code-laptop --owner alice --scopes write,read   # prints the token once
worklog token list [--owner alice]
worklog token revoke claude-code-laptop --owner alice
```

Signed in through the proxy, the web view does the same under **Tokens** for your own tokens. Revoked names cannot be reused, so entries keep a unique agent name.

Tokens look like `wl_…` (32 random bytes). Store them in a password manager or the client configuration, never in prompts or agent instructions.

## Export and backup

```bash
worklog export --owner alice --since 2026-11-09 --until 2026-11-16   # Markdown, grouped by day and project
worklog export --owner alice --format csv --project website -o website.csv
worklog export --owner alice --format json -o worklog.json               # machine-readable, see below
worklog backup --keep 14   # consistent copy to backups/ next to the database, keeps the newest 14
```

### JSON export and import

The JSON export is meant for programs and for moving entries between instances (e.g. from the one at work to the one at home):

```bash
worklog export --owner alice --format json -o worklog.json              # or "JSON" in the web view
docker exec -i worklog-mcp worklog import --owner bob - < worklog.json   # or "Import" in the web view
```

```json
{
  "format": "worklog-export", "version": 1, "exported_at": "2026-11-19T09:00:00+01:00",
  "timezone": "Europe/Berlin", "filters": {"project": "website"},
  "entries": [
    {"external_id": "worklog:3f9c0a1b2c3d4e5f:812", "ts": "2026-11-10T10:00:00+01:00",
     "created_at": "2026-11-10T10:02:13+01:00", "agent": "laptop", "source": "tool",
     "project": "website", "summary": "Startseite gebaut", "details": null,
     "tags": ["deploy"], "duration_min": 30, "session_id": null}
  ]
}
```

- The file contains no owner and no database ids. On import the entries belong to the importing user (the web view's sign-in, or `--owner`); agent, source, times and contents are kept.
- Imports are repeatable: `external_id` is the entry's import id, or `worklog:<instance>:<id>` for entries written directly, and known ids are skipped (`--update` / `?update=true` overwrites them). Importing an export back into the instance it came from skips entries that still exist. Entries without `external_id` (hand-written files) get one derived from time, project and summary.
- Required per entry: `ts`, `project`, `summary`; everything else is optional. A plain JSON list of entries is accepted too.

**Weekly report:** `worklog report --owner alice --period last-week` prints the Markdown report of the last calendar week (`this-week`, `last-month`, `this-month` also work). With `--dir DIR` it writes `worklog-alice-2026-W46.md` there instead and prints one line for a notification, e.g. `2026-W46: 42 entries, 31.5 h, 7 projects (top: website, heimnetz, unraid)`. Run it every Monday morning, e.g. via cron or the Unraid User Scripts plugin:

```bash
line=$(docker exec worklog-mcp worklog report --owner alice --period last-week --dir /backups/reports) \
  && /usr/local/emhttp/webGui/scripts/notify -e worklog -s "Worklog: last week" -d "$line"
```

## Importing earlier sessions

`worklog-import` turns past Claude Code sessions, claude.ai conversations and JetBrains Junie tasks into entries, with their original time stamps. It runs where the data lives (usually your workstation), needs only Python 3.9+ and no extra packages, and uploads finished summaries only – never raw transcripts.

```bash
# from a checkout, without installing:
export PYTHONPATH=/path/to/worklog-mcp/src
cp import-rules.example.yaml import-rules.yaml   # map folders/keywords to projects

# 1. extract + summarize -> import-preview.jsonl and import-preview.md
python -m worklog_import prepare --rules import-rules.yaml --since 2026-10-01 --limit 10
python -m worklog_import prepare --source claude-ai --path ~/Downloads/data-export.zip --rules import-rules.yaml
python -m worklog_import prepare --source junie --rules import-rules.yaml    # ~/.junie, no AI needed

# 2. read import-preview.md; delete lines in import-preview.jsonl or set "skip": true

# 3. upload (token with scope write)
export WORKLOG_URL=https://worklog.example.com WORKLOG_TOKEN=wl_...
python -m worklog_import upload            # --update overwrites already imported entries
```

- **Sources:** Claude Code transcripts in `~/.claude/projects/*/*.jsonl` (subagent transcripts are ignored, sessions active in the last 2 hours are skipped), the claude.ai data export (*Settings → Privacy → Export data*; ZIP, folder or `conversations.json`), and the Junie CLI data folder `~/.junie` (only `sessions/` and `history.jsonl` are read; Junie keeps the last 10 sessions).
- **Summaries:** by default with `claude -p`, isolated from your setup (no tools, no MCP servers, no hooks, no session file), so it can never write to the log itself; one entry per result, up to 8 per session, private or trivial conversations are marked as skipped. `--summarizer none` makes one entry per session from the first prompt, without AI. Junie writes its own summary per task, so `--source junie` uses it (`--summarizer native`): one entry per task, project from the working folder (path rules still apply), time until the next prompt as duration.
- **Secrets:** tokens, keys, passwords, private keys, JWTs, credentials in URLs and long random strings are replaced with `[REDACTED:…]` before anything is summarized or uploaded. Check the preview anyway.
- **Repeatable:** summaries are cached in `import-cache.json`; entries carry `external_id` `claude-code:<session>:<n>` / `claude-ai:<uuid>:<n>` / `junie:<session>:<n>`, so running the import again only adds new sessions.
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
| `WORKLOG_LEGACY_OWNER` | – | user who gets entries and tokens from before multi-user support; needed once, for the upgrade |
| `WORKLOG_OWNER` | – | stdio only: the user whose log this is (required with `--stdio`) |
| `WORKLOG_AGENT` | `stdio` | stdio only: agent name for entries (letters, digits, `-`, `_`, `.`) |
| `WORKLOG_LOGOUT_URL` | – | sign-out link shown to proxy users, e.g. `https://auth.example.com/logout` |

Run: `worklog-server` (or `python -m worklog.server`) for HTTP, `worklog-server --stdio` for stdio. `GET /healthz` answers `ok` without a token (for health checks); everything under `/mcp` needs `Authorization: Bearer <token>`.

## Development

```bash
./scripts/test.sh          # pytest in a python:3.12-slim container, dependencies cached in a Docker volume
./scripts/test.sh -k auth  # extra pytest arguments are passed through
```

The tests start the real server on `127.0.0.1` with a temporary database and talk to it with the FastMCP client; no outside network is used.

## License

MIT, see [LICENSE](LICENSE).
