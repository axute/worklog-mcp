"""Grouping and export formats (used by summarize_period and `worklog export`)."""

from __future__ import annotations

import csv
import io
from collections import OrderedDict
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
