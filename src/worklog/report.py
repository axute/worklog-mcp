"""Grouping and export formats (used by summarize_period and `worklog export`)."""

from __future__ import annotations

import csv
import io
from collections import OrderedDict
from datetime import datetime, time, timedelta
from typing import Any

from .db import Entry


def group_by_day_and_project(entries: list[Entry], tz) -> dict[str, Any]:
    """Entries (oldest first) -> {"days": [{date, duration_min, projects: [{project, duration_min, entries}]}],
    "totals": {entries, duration_min, projects: {name: {entries, duration_min}}}}."""
    days: "OrderedDict[str, OrderedDict[str, list[Entry]]]" = OrderedDict()
    for e in entries:
        day = e.ts.astimezone(tz).date().isoformat()
        days.setdefault(day, OrderedDict()).setdefault(e.project, []).append(e)

    totals: dict[str, dict[str, int]] = {}
    out_days = []
    for day, projects in days.items():
        out_projects = []
        day_minutes = 0
        for project, items in projects.items():
            minutes = sum(i.duration_min or 0 for i in items)
            day_minutes += minutes
            t = totals.setdefault(project, {"entries": 0, "duration_min": 0})
            t["entries"] += len(items)
            t["duration_min"] += minutes
            out_projects.append({
                "project": project,
                "duration_min": minutes,
                "entries": [{
                    "time": i.ts.astimezone(tz).strftime("%H:%M"),
                    "agent": i.agent,
                    "summary": i.summary,
                    "duration_min": i.duration_min,
                    "tags": i.tags,
                } for i in items],
            })
        out_days.append({"date": day, "duration_min": day_minutes, "projects": out_projects})

    return {
        "days": out_days,
        "totals": {
            "entries": len(entries),
            "duration_min": sum(t["duration_min"] for t in totals.values()),
            "projects": totals,
        },
    }


def to_markdown(entries: list[Entry], tz, title: str = "Work log") -> str:
    grouped = group_by_day_and_project(entries, tz)
    lines = [f"# {title}", ""]
    if not entries:
        return "\n".join(lines + ["No entries.", ""])
    for day in grouped["days"]:
        lines.append(f"## {day['date']}")
        lines.append("")
        for p in day["projects"]:
            dur = f" ({p['duration_min']} min)" if p["duration_min"] else ""
            lines.append(f"### {p['project']}{dur}")
            lines.append("")
            for e in p["entries"]:
                extra = f" – {e['duration_min']} min" if e["duration_min"] else ""
                tags = f" `{'`, `'.join(e['tags'])}`" if e["tags"] else ""
                lines.append(f"- {e['time']} · {e['agent']}: {e['summary']}{extra}{tags}")
            lines.append("")
    t = grouped["totals"]
    lines.append(f"**Total:** {t['entries']} entries, {t['duration_min']} min")
    lines.append("")
    return "\n".join(lines)


def to_csv(entries: list[Entry], tz) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "ts", "agent", "source", "project", "summary", "details", "tags", "duration_min", "session_id", "external_id"])
    for e in entries:
        w.writerow([e.id, e.ts.astimezone(tz).isoformat(), e.agent, e.source, e.project, e.summary,
                    e.details or "", ",".join(e.tags), e.duration_min if e.duration_min is not None else "",
                    e.session_id or "", e.external_id or ""])
    return buf.getvalue()


def stats(entries: list[Entry], tz) -> dict[str, Any]:
    """Totals, per project and per day (local date) for the web overview."""
    projects: dict[str, dict[str, int]] = {}
    days: dict[str, dict[str, int]] = {}
    minutes = 0
    for e in entries:
        m = e.duration_min or 0
        minutes += m
        p = projects.setdefault(e.project, {"entries": 0, "duration_min": 0})
        p["entries"] += 1
        p["duration_min"] += m
        d = days.setdefault(e.ts.astimezone(tz).date().isoformat(), {"entries": 0, "duration_min": 0})
        d["entries"] += 1
        d["duration_min"] += m
    return {
        "totals": {"entries": len(entries), "duration_min": minutes, "projects": len(projects), "days": len(days)},
        "projects": sorted(({"project": k, **v} for k, v in projects.items()),
                           key=lambda x: (-x["duration_min"], -x["entries"], x["project"])),
        "days": [{"date": k, **days[k]} for k in sorted(days)],
    }


PERIODS = ("this-week", "last-week", "this-month", "last-month")


def period_bounds(kind: str, tz, now: datetime | None = None, ago: int | None = None) -> tuple[datetime, datetime, str]:
    """(start, end, label) of a calendar week (Monday to Monday) or month in the time zone tz.

    kind: one of PERIODS, or "week"/"month" together with `ago` (0 = current, 1 = previous, ...).
    The label is the ISO week ("2026-W46") or the month ("2026-11")."""
    if kind in PERIODS:
        unit, ago = kind.split("-")[1], (0 if kind.startswith("this") else 1)
    elif kind in ("week", "month"):
        unit, ago = kind, int(ago or 0)
    else:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    if ago < 0:
        raise ValueError("ago must not be negative")
    today = (now or datetime.now(tz)).astimezone(tz).date()
    if unit == "week":
        monday = today - timedelta(days=today.weekday()) - timedelta(weeks=ago)
        start = datetime.combine(monday, time(), tz)
        end = datetime.combine(monday + timedelta(weeks=1), time(), tz)
        year, week, _ = monday.isocalendar()
        return start, end, f"{year}-W{week:02d}"
    month_index = today.year * 12 + today.month - 1 - ago
    year, month = divmod(month_index, 12)
    start = datetime(year, month + 1, 1, tzinfo=tz)
    ny, nm = divmod(month_index + 1, 12)
    end = datetime(ny, nm + 1, 1, tzinfo=tz)
    return start, end, f"{year}-{month + 1:02d}"


def summary_line(entries: list[Entry], label: str) -> str:
    """One line for notifications: '2026-W46: 42 entries, 31.5 h, 7 projects (top: a, b, c)'."""
    minutes = sum(e.duration_min or 0 for e in entries)
    per_project: dict[str, int] = {}
    for e in entries:
        per_project[e.project] = per_project.get(e.project, 0) + (e.duration_min or 0)
    top = sorted(per_project, key=lambda p: (-per_project[p], p))[:3]
    hours = f"{minutes / 60:.1f}".rstrip("0").rstrip(".")
    line = f"{label}: {len(entries)} entries, {hours} h, {len(per_project)} projects"
    return line + (f" (top: {', '.join(top)})" if top else "")
