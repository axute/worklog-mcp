# worklog

An MCP server that collects work log entries from AI agents (Claude Code, Docker Agent, claude.ai and other MCP clients) and makes them searchable again.

- One endpoint, Streamable HTTP, stateless: `https://<your-host>/mcp`
- One bearer token per agent; the server takes the agent name from the token, so entries cannot be written under another name
- SQLite storage (WAL), tokens stored only as SHA-256 hashes
- Times stored in UTC, shown in the configured time zone

> Status: early development. Phase 1 (core server, `log_entry`, `list_entries`, token auth) is done. Search, summaries, REST API, container setup and import follow.

## Tools

| Tool | Parameters | Returns |
|---|---|---|
| `log_entry` | `project`, `summary` (max. 300 chars), optional `details`, `tags`, `duration_min`, `session_id` | id and timestamp |
| `list_entries` | optional `project`, `agent`, `since`, `until`, `limit` (default 20, max. 500) | entries, newest first |

Dates without a time zone (`2026-11-13`) are read in the server's time zone.

Scopes: `write` is needed for `log_entry`, `read` for `list_entries`.

## Tokens

```bash
worklog token create claude-code-laptop --scopes write,read   # prints the token once
worklog token list
worklog token revoke claude-code-laptop
```

Tokens look like `wl_…` (32 random bytes). Store them in a password manager or the client configuration, never in prompts or agent instructions.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WORKLOG_DB` | `/data/worklog.db` | SQLite file |
| `WORKLOG_HOST` | `0.0.0.0` | listen address |
| `WORKLOG_PORT` | `8000` | listen port |
| `WORKLOG_TZ` / `TZ` | `Europe/Berlin` | display time zone |

Run: `python -m worklog.server`. `GET /healthz` answers `ok` without a token (for health checks); everything under `/mcp` needs `Authorization: Bearer <token>`.

## Development

```bash
./scripts/test.sh          # pytest in a python:3.12-slim container, dependencies cached in a Docker volume
./scripts/test.sh -k auth  # extra pytest arguments are passed through
```

The tests start the real server on `127.0.0.1` with a temporary database and talk to it with the FastMCP client; no outside network is used.
