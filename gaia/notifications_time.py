"""When a reminder or routine is due, computed in the machine's local wall clock.

Instants are stored as UTC ISO strings; the user's hours (an ``--at`` without a
zone, a cron hour) are local wall time, so a routine at 17:00 stays at 17:00
across a daylight-saving change. No tzdata is needed: the conversion is the
operating system's own local-time rule.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"
_IANA_NAME = re.compile(r"^[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*$")
_DURATION = re.compile(r"^(\d+)([mhdw])$")
_DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_CRON_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day_of_month", 1, 31),
    ("month", 1, 12),
    ("day_of_week", 0, 7),
)
# Long enough for a Feb 29 constrained to one weekday (28 years).
_CALENDAR_HORIZON_DAYS = 366 * 29


def now_utc() -> datetime:
    """The current instant, timezone-aware in UTC."""
    return datetime.now(timezone.utc)


def to_iso(instant: datetime) -> str:
    """Render an aware instant as the stored UTC string."""
    return instant.astimezone(timezone.utc).strftime(_ISO_UTC)


def from_iso(value: str) -> datetime:
    """Parse a stored UTC string back into an aware instant."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_local(instant: datetime) -> str:
    """Render an instant as the local date and hour the user reads."""
    return instant.astimezone().strftime("%a %Y-%m-%d %H:%M %Z")


def _iana_name(text: str) -> str | None:
    _, marker, tail = text.partition("zoneinfo/")
    name = tail if marker else text
    return name if _IANA_NAME.match(name) else None


def zone_name() -> str | None:
    """The IANA name of the machine's local zone, or None when none can be read.

    The stdlib gives only an abbreviation (``-03``), so the name comes from what
    decides local time: ``TZ`` when set, else ``/etc/timezone``, else the target
    of ``/etc/localtime``.
    """
    tz = os.environ.get("TZ")
    if tz is not None:
        return _iana_name(tz.lstrip(":"))
    try:
        name = _iana_name(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    except OSError:
        name = None
    if name:
        return name
    target = os.path.realpath("/etc/localtime")
    return _iana_name(target) if "zoneinfo/" in target else None


def clock_reading(instant: datetime) -> dict:
    """The instant as this machine reads it: local time, UTC offset, zone and UTC.

    ``zone`` falls back to the offset when no zone name resolves.
    """
    local = instant.astimezone()
    digits = local.strftime("%z")
    offset = f"{digits[:3]}:{digits[3:]}"
    return {
        "local": local.isoformat(timespec="seconds"),
        "offset": offset,
        "zone": zone_name() or offset,
        "utc": to_iso(instant),
    }


def clock_line(instant: datetime) -> str:
    """The one line naming the current local time and zone, e.g. ``now: Thu 2026-10-01 11:20 -03:00 (America/Santiago)``."""
    reading = clock_reading(instant)
    line = f"now: {instant.astimezone():%a %Y-%m-%d %H:%M} {reading['offset']}"
    return line if reading["zone"] == reading["offset"] else f"{line} ({reading['zone']})"


def pointer_label(row: dict) -> str:
    """What a notification points at, as the user would name it, or "" when nothing."""
    kind, ref, workspace = row.get("pointer_kind"), row.get("pointer_ref"), row.get("pointer_workspace")
    if kind == "skill":
        return f"skill {ref}"
    if kind == "memory":
        return f"memory {workspace}:{ref}"
    if kind == "project":
        return f"project {workspace}/{ref}"
    return ""


def summary(row: dict) -> str:
    """One line naming a notification, when it is due and what it points at."""
    line = f"{row['kind']} #{row['id']} '{row['headline']}'"
    if row.get("due_at"):
        line += f" due {format_local(from_iso(row['due_at']))}"
    pointer = pointer_label(row)
    return f"{line} -> {pointer}" if pointer else line


def parse_at(value: str, now: datetime) -> datetime:
    """Read ``--at``: a value without a zone is local wall time; it must lie after ``now``."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            f"--at {value!r} needs a date and time, YYYY-MM-DDTHH:MM; {clock_line(now)} "
            "-- read the time with `gaia now` and give the absolute --at"
        ) from None
    instant = parsed.astimezone(timezone.utc)
    if instant <= now:
        raise ValueError(
            f"--at {value!r} ({format_local(instant)}) is not in the future; {clock_line(now)}"
        )
    return instant


def parse_duration(value: str) -> timedelta:
    """Read a positive duration such as ``90m``, ``2h``, ``3d`` or ``1w``."""
    match = _DURATION.match(value.strip())
    if not match or int(match.group(1)) == 0:
        raise ValueError(f"cannot read duration {value!r}; use N followed by m, h, d or w")
    return timedelta(**{_DURATION_UNITS[match.group(2)]: int(match.group(1))})


def interval_spec(value: str) -> dict:
    """Recurrence every fixed duration, from ``--every``."""
    return {"kind": "interval", "seconds": int(parse_duration(value).total_seconds())}


def _cron_field(text: str, name: str, low: int, high: int) -> list[int] | None:
    if text == "*":
        return None
    values: set[int] = set()
    for part in text.split(","):
        body, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if body == "*":
            start, end = low, high
        elif "-" in body:
            start_text, end_text = body.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = int(body)
            end = high if step_text else start
        if step < 1 or not low <= start <= end <= high:
            raise ValueError(f"cron {name} {part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    if name == "day_of_week" and 7 in values:
        values.discard(7)
        values.add(0)
    return sorted(values)


def cron_spec(expression: str) -> dict:
    """Recurrence from a five-field cron expression, in local wall time."""
    fields = expression.split()
    if len(fields) != len(_CRON_FIELDS):
        raise ValueError(f"cron {expression!r} needs five fields: minute hour day month weekday")
    spec: dict = {"kind": "calendar"}
    try:
        for text, (name, low, high) in zip(fields, _CRON_FIELDS):
            spec[name] = _cron_field(text, name, low, high)
    except ValueError as exc:
        raise ValueError(f"cannot read cron {expression!r}: {exc}") from None
    return spec


def _day_matches(spec: dict, day: date) -> bool:
    if spec["month"] is not None and day.month not in spec["month"]:
        return False
    by_date = spec["day_of_month"] is None or day.day in spec["day_of_month"]
    by_weekday = spec["day_of_week"] is None or (day.weekday() + 1) % 7 in spec["day_of_week"]
    if spec["day_of_month"] is not None and spec["day_of_week"] is not None:
        return by_date or by_weekday
    return by_date and by_weekday


def next_occurrence(spec: dict, after: datetime, anchor: datetime | None = None) -> datetime:
    """First occurrence of ``spec`` strictly after ``after``; missed ones collapse into it.

    An interval keeps the phase of ``anchor`` (the previous due instant) when given.
    """
    if spec["kind"] == "interval":
        step = timedelta(seconds=spec["seconds"])
        start = anchor or after
        if start > after:
            return start
        return start + ((after - start) // step + 1) * step

    day = after.astimezone().date()
    hours = spec["hour"] if spec["hour"] is not None else range(24)
    minutes = spec["minute"] if spec["minute"] is not None else range(60)
    for _ in range(_CALENDAR_HORIZON_DAYS):
        if _day_matches(spec, day):
            for hour in hours:
                for minute in minutes:
                    candidate = datetime(day.year, day.month, day.day, hour, minute).astimezone()
                    if candidate > after:
                        return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)
    raise ValueError("the recurrence never occurs")
