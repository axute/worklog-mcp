"""Notifications through Apprise: targets are URLs like ntfy://topic, discord://id/token,
tgram://bot/chat, mailto://user:pass@host, matrix://... (see https://appriseit.com).

URLs often contain tokens, so they come only from the environment or the command line
and are never logged or shown."""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("worklog.notify")


def notify_urls(extra: list[str] | None = None) -> list[str]:
    """Targets from WORKLOG_NOTIFY (separated by spaces, commas or new lines) plus `extra`."""
    urls = [u for u in re.split(r"[\s,]+", os.environ.get("WORKLOG_NOTIFY", "")) if u]
    return urls + [u for u in (extra or []) if u]


def send(urls: list[str], title: str, body: str) -> tuple[int, int]:
    """Send a Markdown message to every target. Returns (sent, failed); invalid URLs count as failed."""
    import apprise  # imported here: only needed when notifications are used

    sent = failed = 0
    for i, url in enumerate(urls, 1):
        target = apprise.Apprise()
        if not target.add(url):
            log.warning("notify_invalid_target", extra={"status": f"target {i}"})
            failed += 1
            continue
        if target.notify(title=title, body=body, body_format=apprise.NotifyFormat.MARKDOWN):
            sent += 1
        else:
            log.warning("notify_failed", extra={"status": f"target {i}"})
            failed += 1
    return sent, failed


def report_message(entries, label: str, project: str | None = None, top: int = 8) -> tuple[str, str]:
    """(title, Markdown body) for a period report: totals and the largest projects."""
    from .report import summary_line
    minutes: dict[str, int] = {}
    counts: dict[str, int] = {}
    for e in entries:
        minutes[e.project] = minutes.get(e.project, 0) + (e.duration_min or 0)
        counts[e.project] = counts.get(e.project, 0) + 1
    title = f"Worklog {label}" + (f" · {project}" if project else "")
    lines = [summary_line(entries, label)]
    if entries:
        lines.append("")
        for name in sorted(minutes, key=lambda p: (-minutes[p], -counts[p], p))[:top]:
            hours = f"{minutes[name] / 60:.1f}".rstrip("0").rstrip(".")
            lines.append(f"- **{name}**: {hours} h, {counts[name]} entries")
        if len(minutes) > top:
            lines.append(f"- … and {len(minutes) - top} more")
    return title, "\n".join(lines)
