# Configuration

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
| `WORKLOG_NOTIFY` | – | Apprise URLs for `worklog report --notify` and `worklog notify-test` on the command line, separated by spaces or commas (users set up their own targets in the web view) |
| `WORKLOG_NOTIFY_ALLOW` | – | extra Apprise schemes users may add in the web view, e.g. `json` for your own webhook (separated by spaces or commas) |
| `WORKLOG_NOTIFY_DENY` | – | schemes to remove from the allowed list, e.g. `mailto mailtos` |
| `WORKLOG_SCHEDULER` | `on` | `off` stops the server from sending scheduled reports (sending by hand still works) |
| `WORKLOG_OWNER` | – | stdio only: the user whose log this is (required with `--stdio`) |
| `WORKLOG_AGENT` | `stdio` | stdio only: agent name for entries (letters, digits, `-`, `_`, `.`) |
| `WORKLOG_LOGOUT_URL` | – | sign-out link shown to proxy users, e.g. `https://auth.example.com/logout` |

Run: `worklog-server` (or `python -m worklog.server`) for HTTP, `worklog-server --stdio` for stdio. `GET /healthz` answers `ok` without a token (for health checks); everything under `/mcp` needs `Authorization: Bearer <token>`.
