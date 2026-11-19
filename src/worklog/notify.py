"""Notifications through Apprise: targets are URLs like ntfy://topic, discord://id/token,
tgram://bot/chat, mailto://user:pass@host, matrix://... (see https://appriseit.com).

URLs often contain tokens, so they come only from the environment or the command line
and are never logged or shown."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime

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


# ------------------------------------------------------------------ per-user targets (web view)

# Known chat, push and mail services. Generic webhooks (json://, xml://, form://) and relays
# (apprise://) are not in the list: they can call any address, also inside your network.
# Self-hostable services such as ntfy, Gotify, Matrix or e-mail may point to your own servers.
DEFAULT_SCHEMES = frozenset("""
    ntfy ntfys discord tgram slack matrix matrixs gotify gotifys pover pbul mailto mailtos signal signals
    rocket rockets mmost mmosts zulip gchat threema whatsapp workflows bark barks pushme line
""".split())


def allowed_schemes(settings) -> frozenset[str]:
    """DEFAULT_SCHEMES plus WORKLOG_NOTIFY_ALLOW, minus WORKLOG_NOTIFY_DENY."""
    return (DEFAULT_SCHEMES | settings.notify_allow) - settings.notify_deny


def services(settings) -> list[dict]:
    """Allowed services with their URL schemes and the link to Apprise's setup page."""
    import apprise
    allowed = allowed_schemes(settings)
    out = []
    for s in apprise.Apprise().details()["schemas"]:
        schemes = [p for p in (s.get("protocols") or ()) + (s.get("secure_protocols") or ()) if p in allowed]
        if schemes:
            out.append({"name": s["service_name"], "schemes": sorted(set(schemes)), "docs": s.get("setup_url")})
    return sorted(out, key=lambda x: x["name"].lower())


def scheme_of(url: str) -> str:
    return url.split("://", 1)[0].lower() if "://" in url else ""


def check_target(url: str, settings) -> str:
    """Validated URL, or ValueError (unknown or not allowed service, or not understood by Apprise)."""
    import apprise
    url = (url or "").strip()
    if len(url) > 2000:
        raise ValueError("URL is too long")
    scheme = scheme_of(url)
    if not scheme:
        raise ValueError("expected an Apprise URL like ntfy://my-topic")
    if scheme not in allowed_schemes(settings):
        raise ValueError(f"the service {scheme}:// is not allowed on this server")
    if not apprise.Apprise().add(url):
        raise ValueError(f"Apprise does not understand this {scheme}:// URL; check the service's documentation")
    return url


def mask(url: str) -> str:
    """ntfy://…opic: the scheme and the last 4 characters, never the rest (URLs hold tokens)."""
    scheme, _, rest = url.partition("://")
    rest = rest.rstrip("/")
    return f"{scheme}://…{rest[-4:]}" if len(rest) >= 8 else f"{scheme}://…"


def send_to_targets(db, owner: str, settings, title: str, body: str, only: int | None = None) -> tuple[int, int]:
    """Send to the user's targets (or just target `only`); stores the result per target."""
    sent = failed = 0
    allowed = allowed_schemes(settings)
    for t in db.notify_targets(owner=owner):
        if only is not None and t["id"] != only:
            continue
        error = None
        if scheme_of(t["url"]) not in allowed:
            error = "service not allowed (any more) on this server"
        else:
            try:
                ok, bad = send([t["url"]], title, body)
                error = None if ok else "delivery failed (see the service's documentation and the server log)"
            except Exception as e:  # never let one target break the others
                error = f"delivery failed: {type(e).__name__}"
        db.notify_result(t["id"], error)
        sent, failed = (sent + 1, failed) if not error else (sent, failed + 1)
    return sent, failed


# ------------------------------------------------------------------ scheduler

def due_reports(schedule: dict, now: datetime) -> list[str]:
    """Report periods due at `now` (local time): 'last-week' from the scheduled weekday and time on
    (until the end of that week, so a restart catches up), 'last-month' from the 1st at that time."""
    from datetime import time as dtime, timedelta
    hh, mm = (int(x) for x in schedule["time"].split(":"))
    due = []
    if schedule["weekly"]:
        monday = now.date() - timedelta(days=now.weekday())
        moment = datetime.combine(monday + timedelta(days=schedule["weekday"]), dtime(hh, mm), now.tzinfo)
        if now >= moment:
            due.append("last-week")
    if schedule["monthly"]:
        moment = datetime.combine(now.date().replace(day=1), dtime(hh, mm), now.tzinfo)
        if now >= moment:
            due.append("last-month")
    return due


def send_report(db, owner: str, settings, period: str, now: datetime | None = None) -> str:
    """Send the report of `period` to all targets of the user; returns a short result."""
    from .report import period_bounds
    start, end, label = period_bounds(period, settings.timezone, now)
    entries = db.entries_between(start, end, owner=owner)
    title, body = report_message(entries, label)
    if not db.notify_targets(owner=owner):
        return "no targets"
    sent, failed = send_to_targets(db, owner, settings, title, body)
    return f"sent {sent}, failed {failed}"


def run_due_reports(db, settings, now: datetime | None = None) -> list[tuple[str, str, str]]:
    """One scheduler pass: every due, not yet sent report of every user. Returns (owner, label, result)."""
    from .report import period_bounds
    now = (now or datetime.now(settings.timezone)).astimezone(settings.timezone)
    done = []
    for owner in db.scheduled_owners():
        for period in due_reports(db.report_schedule(owner=owner), now):
            kind = period.split("-")[1]
            label = period_bounds(period, settings.timezone, now)[2]
            if db.report_sent(owner=owner, kind=kind, label=label):
                continue
            result = send_report(db, owner, settings, period, now)
            db.record_report(owner=owner, kind=kind, label=label, result=result)
            log.info("report_sent", extra={"by": owner, "status": f"{label}: {result}"})
            done.append((owner, label, result))
    return done


def start_scheduler(db, settings) -> None:
    """Background thread: checks for due reports once a minute (HTTP server only)."""
    import threading
    import time as _time

    def loop():
        while True:
            _time.sleep(60 - datetime.now().second % 60 + 1)
            try:
                run_due_reports(db, settings)
            except Exception:
                log.exception("scheduler_failed")

    threading.Thread(target=loop, name="worklog-scheduler", daemon=True).start()
