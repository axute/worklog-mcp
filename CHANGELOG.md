# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/). Images: `ghcr.io/axute/worklog-mcp:<version>`.

## [Unreleased]

## [1.0.0] – 2026-11-19

First release. Everything below is new.

### For agents (MCP)
- Tools `log_entry`, `log_entries` (up to 100 at once), `update_entry` (own entries only), `list_entries`, `search_entries` (full text), `summarize_period`, `list_projects` (with your project notes).
- Entries for earlier work with `ts`; `log_entry` reports likely duplicates.
- Prompts `weekly_report`, `monthly_report`, `standup`, `backfill_project` and `backfill_my_commits` (history of the open project from your own commits, with a preview before writing).
- Streamable HTTP with one bearer token per agent, or stdio (`worklog-server --stdio`, e.g. `docker run -i`).

### Web view
- Entries with search and filters, overview with activity calendar and charts, timesheet (day/week/month, rounding, CSV), projects with description, customer, color and archive flag.
- Entries by hand, corrections, delete, rename and merge projects, duplicate detection with merge or dismiss.
- Export as Markdown, CSV or JSON; JSON import.
- Sign-in through a reverse proxy (e.g. Authelia) with token management, or with a token; the token's scopes decide whether changes are allowed.

### Data and operations
- Several users, strictly separated; SQLite with WAL and full text search; online backups.
- `worklog` command line: tokens, export, JSON import, weekly/monthly report, notifications through Apprise (`WORKLOG_NOTIFY`), demo data.
- `worklog-import` for earlier Claude Code sessions, claude.ai exports, Microsoft Copilot CSV and JetBrains Junie.
- Container image for amd64 and arm64 on ghcr.io, built by GitHub Actions after the tests.

### Upgrading from an unreleased version
- Database migrations run on start; take a backup first (`worklog backup`).
- Databases from before multi-user support need `WORKLOG_LEGACY_OWNER=<user>` once.
- The CLI needs `--owner` for `token create`, `token revoke` and `export`.

[Unreleased]: https://github.com/axute/worklog-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/axute/worklog-mcp/releases/tag/v1.0.0
