# Users, tokens and data

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

### Notifications

Each user can set up targets and a weekly or monthly schedule in the web view under **Settings**; the server then sends the reports itself – see [Reports by notification](web.md#reports-by-notification). The command line below is for admins and cron jobs.

Reports can also go to ntfy, Discord, Telegram, Matrix, Slack, e-mail and [many more services](https://appriseit.com/services/) through [Apprise](https://github.com/caronc/apprise). A target is a URL, for example `ntfy://my-topic`, `discord://webhook_id/webhook_token`, `tgram://bot_token/chat_id` or `mailto://user:password@example.com`. Put one or more (separated by spaces or commas) into `WORKLOG_NOTIFY`, or pass `--notify-url` (repeatable):

```bash
docker exec worklog-mcp worklog notify-test                                   # sends a test message
docker exec worklog-mcp worklog report --owner alice --period last-week --dir /backups/reports --notify
```

The message has the summary line and the largest projects; the full report stays in the file. The URLs often contain tokens: keep them in `.env`, not in the compose file you share.

## Demo data

`worklog demo-data --owner demo` fills an empty user with about 250 made-up entries of the last 120 days (five projects, with details, colors and an archived project), to try the web view or take screenshots without real data. Sign in as that user (with proxy sign-in) or create a token for it with `worklog token create … --owner demo`.

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
