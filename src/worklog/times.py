"""Reading times given by users and agents."""

from __future__ import annotations

from datetime import datetime, timedelta


def work_time(value: str | None, tz, now: datetime | None = None) -> datetime | None:
    """When the work was done: None = now; a date alone means noon of that day (local)."""
    if not value or not value.strip():
        return None
    v = value.strip()
    when = parse_local(v + "T12:00" if len(v) == 10 else v, tz)
    now = now or datetime.now(tz)
    if when > now + timedelta(minutes=5):
        raise ValueError(f"ts {value} is in the future")
    if when.year < 1990:
        raise ValueError(f"ts {value} is implausibly old")
    return when


def parse_local(value: str | None, tz) -> datetime | None:
    """Dates without a zone are meant in the display time zone (e.g. '2026-11-13' = local midnight)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"'{value}' is not an ISO date or datetime") from e
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)
