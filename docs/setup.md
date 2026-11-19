# Setup guide

From an empty server to agents that log on their own, reachable over HTTPS. You need a host with Docker and Docker Compose, and a way to reach the server over HTTPS.

**Who connects from where** decides how reachable the server must be:

| Client | Connects from | Needs |
|---|---|---|
| Claude Code, IDE agents, scripts | your machine | reachable from your machine (LAN/VPN is fine) |
| claude.ai, Claude Desktop connectors, Claude mobile | Anthropic's cloud | a **public** HTTPS URL (e.g. via a tunnel); the bearer token protects it |

## 1. Get the code and a data directory

The compose files are in the repository; the image itself can come from `ghcr.io/axute/worklog-mcp` (see below) or be built from the checkout.

```bash
git clone https://github.com/axute/worklog-mcp.git
cd worklog-mcp
mkdir -p /srv/worklog-mcp && chown 99:100 /srv/worklog-mcp   # the container runs as UID 99 / GID 100
```

Put the data directory on a real file system: SQLite file locks are unreliable on FUSE/union mounts (on Unraid use `/mnt/cache/...` or `/mnt/diskN/...`, not `/mnt/user/...`).

## 2. Configure

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

## 3. Start and check

```bash
docker compose up -d
docker inspect --format '{{.State.Health.Status}}' worklog-mcp     # -> healthy (after ~10 s)
curl -i https://worklog.example.com/healthz                         # -> 200 ok
curl -i -X POST https://worklog.example.com/mcp                     # -> 401: no token, as it should be
```

## 4. Create a token

One token per agent or device; its name becomes the `agent` of every entry written with it. `--owner` is your user name (with proxy sign-in, use the name your proxy reports, so the web view shows the same data):

```bash
docker exec -it worklog-mcp worklog token create claude-code-laptop --owner alice --scopes write,read
```

The token (`wl_…`) is shown once. Keep it in a password manager or the client configuration, never in prompts or agent instructions. With proxy sign-in (see [Web view](web.md)) you can do this in the browser under **Tokens** instead.

## 5. Connect your clients

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

Scripts and hooks that don't speak MCP use the [REST API](tools.md#rest-api) with the same token.

## 6. Tell the agent when to log

Agents log reliably only with an explicit rule in the instructions they load in every session. Copy the block from [`integrations/agent-rule.md`](../integrations/agent-rule.md) (English or German) into `~/.claude/CLAUDE.md` for Claude Code, into the personal preferences or project instructions in claude.ai, or into the global instructions of your other agents.

## 7. Optional: Claude Code reminder hook

[`integrations/claude-code/worklog-stop.py`](../integrations/claude-code/worklog-stop.py) is a *Stop* hook. When Claude finishes a turn in which it changed something (file edits, writing shell commands such as `git commit`, `docker compose up` or `rm`) and has not called `log_entry` afterwards, the hook sends it back once with a reminder. It never writes to the log itself, never reminds twice in a row, and does nothing if it fails. It needs Python 3 on the machine.

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

## 8. Try it

Start a **new** session (MCP servers and instructions are loaded at start), let the agent finish something small, and check that an entry arrives: in the web view at `https://worklog.example.com/ui/`, or with

```bash
docker exec worklog-mcp worklog export --owner alice --since 2026-01-01 | tail
```

## 9. Backups and updates

- **Backup**, e.g. nightly via cron or the Unraid User Scripts plugin: `docker exec worklog-mcp worklog backup --keep 14`. This writes a consistent copy to `backups/` in the data directory and keeps the newest 14. To keep backups elsewhere, mount a second volume (see `compose.example.yaml`) and add `--dir /backups`; that directory must be writable for UID 99 / GID 100.
- **Update:** with the published image `docker compose pull && docker compose up -d`; when building from the checkout `git pull`, then `docker compose build && docker compose up -d --force-recreate`. A plain restart keeps running the old image. Database migrations run on start.
- **Images:** `ghcr.io/axute/worklog-mcp` is built by GitHub Actions after the tests pass: `:latest` follows `main`, `:sha-<commit>` pins a build, release tags `v1.2.3` also publish `:1.2.3`, `:1.2` and `:1`.
- **Older installations** without user support: see [Users](data.md#users) before updating.

## Optional next steps

- [Web view with sign-in through your reverse proxy](web.md#signing-in-through-a-reverse-proxy-recommended), including token management in the browser and more users.
- [Import earlier sessions](data.md#importing-earlier-sessions) from Claude Code, claude.ai or JetBrains Junie.

## Alternative: let the client start it (stdio, `docker run -i`)

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
