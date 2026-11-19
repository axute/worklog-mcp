# MCP tools, prompts and REST API

## Tools

| Tool | Parameters | Returns |
|---|---|---|
| `log_entry` | `project`, `summary` (max. 300 chars), optional `details`, `tags`, `duration_min`, `session_id`, `ts` | id and timestamp; `possible_duplicate_of` with a hint if a very similar entry exists close to that time |
| `log_entries` | `entries`: up to 100 objects with the fields of `log_entry` | ids per entry; all are checked before any is written |
| `update_entry` | `entry_id`, and any of `project`, `summary`, `details`, `tags`, `duration_min` | the corrected entry; only entries of the same agent (token) |
| `list_entries` | optional `project`, `agent`, `since`, `until`, `limit` (default 20, max. 500) | entries, newest first |
| `search_entries` | `query`, optional `project`, `limit` | full text matches (summary, details, project, tags), best first; every word must match, word beginnings are enough |
| `summarize_period` | `since`, `until`, optional `project` | entries grouped by day and project with durations and totals, e.g. for weekly reports |
| `list_projects` | – | projects with entry count, logged minutes, last entry and your notes (description, customer, archived) |

Dates without a time zone (`2026-11-13`) are read in the server's time zone.

Scopes: `write` is needed for `log_entry`, `log_entries` and `update_entry`, `read` for all other tools.

**Logging afterwards:** `ts` is when the work was done – an ISO date (`2025-03-14`, counted as noon) or datetime, in the server's time zone unless it has an offset; never in the future. The time of logging is kept as well (`created_at`), and the web view marks such entries as *logged afterwards*.

**Prompts** (shown as ready-made commands in Claude Desktop, Claude Code and other clients):

| Prompt | Arguments | Does |
|---|---|---|
| `weekly_report` | `weeks_ago` (0 = this week, default 1 = last week), optional `project`, `language` | asks the model to call `summarize_period` for that calendar week (Monday to Sunday) and write a short report from it |
| `standup` | optional `language` | what you did on the last working day (Friday on Mondays) and today so far, plus open points the entries mention |
| `monthly_report` | `months_ago` (default 1), optional `project`, `language` | the same for a calendar month |
| `backfill_project` | `project`, optional `source` (e.g. a repository path), `since`, `language` | logs the history of an earlier project afterwards: checks what is logged already, reads the git history, changelog and docs, groups them into results and writes them with `log_entries` and the real dates (tagged `backfill`; nothing invented, durations only where the sources show them) |
| `backfill_my_commits` | optional `since`, `language` | the same for the project that is open, from your own commits only: finds the project name (and an existing one in the log), asks which git authors are you, shows a preview and waits for your OK before writing |

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
