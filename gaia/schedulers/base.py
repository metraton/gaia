"""Backend interface, neutral-schedule validation, and platform selection.

The NEUTRAL schedule (schedule_spec) is a tagged union stored as JSON in the
scheduled_tasks table:

    {"kind": "calendar",
     "minute": 30, "hour": 7,           # int | list[int] | null (=any)
     "day_of_month": null, "month": null,
     "day_of_week": [1,2,3,4,5]}        # 0-6, Sun=0

    {"kind": "interval", "every_seconds": 21600}

It is the common subset cron / launchd / schtasks can all express natively, so a
backend translates it to its own form without loss. This module owns the shape
(validate_spec), the human render (render_hint), machine identity, and backend
selection; each backend owns its native translation + materialization.
"""

from __future__ import annotations

import os
import platform
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional

# Marker that tags a Gaia-managed scheduler entry. A sync ONLY rewrites entries
# carrying this marker (plus, once, an adopted unmarked entry it is replacing),
# so hand-written crontab lines are never touched. Kept identical across
# backends: cron uses it in a trailing `# gaia-schedule:<name>` comment; a
# future launchd backend uses it in the plist Label.
MANAGED_MARKER_PREFIX = "gaia-schedule:"

# Neutral calendar fields, in cron column order.
_CALENDAR_FIELDS = ("minute", "hour", "day_of_month", "month", "day_of_week")
_FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day_of_month": (1, 31),
    "month": (1, 12),
    "day_of_week": (0, 7),  # 0 and 7 both = Sunday in cron
}


class SpecError(ValueError):
    """Raised when a schedule_spec is malformed or not translatable."""


@dataclass
class DaemonStatus:
    """Best-effort health of the platform scheduler daemon.

    ``running`` is True/False when known, None when it could not be determined
    (the detection is advisory only and must never raise). ``detail`` is a short
    human string for the CLI / reconciliation block.
    """
    running: Optional[bool]
    detail: str = ""


def validate_spec(spec: Mapping[str, Any]) -> None:
    """Validate a neutral schedule_spec. Raises SpecError on any problem.

    This is platform-independent: it checks the SHAPE, not whether a given
    backend can express it (a backend raises SpecError from translate() when the
    shape is valid but not expressible in its native form).
    """
    if not isinstance(spec, Mapping):
        raise SpecError("schedule_spec must be an object")
    kind = spec.get("kind")
    if kind == "calendar":
        for fname in _CALENDAR_FIELDS:
            if fname not in spec:
                continue
            _validate_calendar_field(fname, spec.get(fname))
        # A calendar spec that pins nothing (every field null) would fire every
        # minute -- almost never intended; require at least one bound field.
        if all(spec.get(f) is None for f in _CALENDAR_FIELDS):
            raise SpecError(
                "calendar schedule pins no field (would run every minute); "
                "set at least one of minute/hour/day_of_week/..."
            )
    elif kind == "interval":
        secs = spec.get("every_seconds")
        if not isinstance(secs, int) or isinstance(secs, bool) or secs <= 0:
            raise SpecError("interval schedule needs a positive integer every_seconds")
    else:
        raise SpecError(f"schedule_spec.kind must be 'calendar' or 'interval', got {kind!r}")


def _validate_calendar_field(name: str, value: Any) -> None:
    if value is None:
        return
    lo, hi = _FIELD_RANGES[name]
    values = value if isinstance(value, list) else [value]
    if not values:
        raise SpecError(f"{name} list is empty")
    for v in values:
        if not isinstance(v, int) or isinstance(v, bool):
            raise SpecError(f"{name} values must be integers, got {v!r}")
        if not (lo <= v <= hi):
            raise SpecError(f"{name}={v} out of range [{lo},{hi}]")


_DOW_NAMES = {0: "Dom", 1: "Lun", 2: "Mar", 3: "Mie", 4: "Jue", 5: "Vie", 6: "Sab", 7: "Dom"}


def render_hint(spec: Mapping[str, Any]) -> str:
    """Human-readable render of a schedule_spec (advisory; not authoritative)."""
    try:
        if spec.get("kind") == "interval":
            secs = int(spec["every_seconds"])
            if secs % 3600 == 0:
                return f"cada {secs // 3600}h"
            if secs % 60 == 0:
                return f"cada {secs // 60}min"
            return f"cada {secs}s"
        # calendar
        hour = spec.get("hour")
        minute = spec.get("minute")
        time_part = ""
        if isinstance(hour, int) and isinstance(minute, int):
            time_part = f"{hour:02d}:{minute:02d}"
        elif isinstance(hour, int):
            time_part = f"{hour:02d}:00"
        dow = spec.get("day_of_week")
        dow_part = ""
        if isinstance(dow, list) and dow:
            dow_part = " " + ",".join(_DOW_NAMES.get(d, str(d)) for d in dow)
        elif isinstance(dow, int):
            dow_part = " " + _DOW_NAMES.get(dow, str(dow))
        return (time_part + dow_part).strip() or "calendario"
    except Exception:
        return "?"


def machine_name() -> str:
    """Return this machine's canonical name (= platform.node()).

    Matches the `machines.name` column the scanner writes and the
    session_manifest `_machine_label()` host part, so a task's machine scope and
    its materialization state key on the same identifier everywhere.
    """
    try:
        return platform.node() or "unknown"
    except Exception:
        return "unknown"


def is_wsl() -> bool:
    """True when running inside WSL (Windows Subsystem for Linux).

    On WSL a scheduled task lives in the DISTRO's cron (the cron backend), never
    the Windows Task Scheduler -- Claude, PATH, credentials, and gaia.db all live
    inside the distro. Best-effort; never raises.
    """
    try:
        if os.environ.get("WSL_DISTRO_NAME"):
            return True
        proc_version = Path("/proc/version")
        if proc_version.is_file():
            text = proc_version.read_text(errors="replace").lower()
            return "microsoft" in text or "wsl" in text
    except Exception:
        pass
    return False


class SchedulerBackend(ABC):
    """Interface every platform backend implements.

    A backend translates the neutral schedule_spec to its native form and
    materializes it. `translate`/`is_installed`/`list_managed`/`ensure_daemon`
    are read-only (T0); `install`/`remove` mutate the OS scheduler and are only
    ever invoked by `gaia schedule sync` (T3).
    """

    name: str = "base"

    @staticmethod
    @abstractmethod
    def available() -> bool:
        """True when this backend's scheduler binary/daemon is usable here."""

    @abstractmethod
    def translate(self, task: Mapping[str, Any]) -> str:
        """Translate a task's neutral schedule_spec to a native artifact.

        Raises SpecError when the (valid) spec is not expressible natively.
        """

    @abstractmethod
    def is_installed(self, name: str) -> bool:
        """True when a Gaia-managed entry for ``name`` exists in the scheduler."""

    @abstractmethod
    def list_managed(self) -> List[str]:
        """Return the task names of all Gaia-managed entries in the scheduler."""

    @abstractmethod
    def install(self, tasks: List[Mapping[str, Any]]) -> List[str]:
        """Materialize ``tasks`` into the scheduler (T3). Return installed names.

        Idempotent: regenerates the whole Gaia-managed block so running twice
        yields an identical scheduler state. Never touches unmarked entries
        except the one-time adoption replacement.
        """

    @abstractmethod
    def remove(self, names: List[str]) -> List[str]:
        """Remove the named Gaia-managed entries from the scheduler (T3)."""

    @abstractmethod
    def ensure_daemon(self) -> DaemonStatus:
        """Report scheduler-daemon health. Never starts it; never raises."""


def select_backend() -> Optional[SchedulerBackend]:
    """Return the backend for the CURRENT platform, or None if none applies.

    Linux (including WSL) -> cron. macOS/Windows are deferred (launchd/schtasks
    interfaces are not yet implemented) and return None so callers degrade
    gracefully rather than crash.
    """
    system = ""
    try:
        system = platform.system().lower()
    except Exception:
        pass
    if system == "linux":
        from .cron import CronBackend
        return CronBackend()
    # macOS ('darwin') -> launchd, Windows -> schtasks: deferred.
    return None
