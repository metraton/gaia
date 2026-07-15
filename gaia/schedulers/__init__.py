"""Pluggable native-scheduler backends for Gaia scheduled tasks.

The desired state of a recurring task lives in gaia.db (the scheduled_tasks
table) as an OS-agnostic NEUTRAL schedule. A backend translates that neutral
schedule into a platform's native scheduler and materializes it there. This
package exposes:

  - SchedulerBackend      -- the backend interface (Protocol + ABC).
  - DaemonStatus          -- best-effort daemon health, never raises.
  - SpecError             -- raised on an invalid / untranslatable schedule_spec.
  - validate_spec         -- validate a neutral schedule_spec dict.
  - machine_name / is_wsl -- machine identity + WSL detection.
  - select_backend        -- choose the backend for the CURRENT platform.
  - MANAGED_MARKER_PREFIX -- the crontab/label marker that tags Gaia-managed
                             entries so a sync only ever rewrites its own.

Only the cron backend is implemented today (Linux, including WSL, where a task
runs in the distro's cron -- NOT the Windows Task Scheduler). launchd (macOS) and
schtasks (Windows) are deferred but the interface is ready for them to plug in.
"""

from __future__ import annotations

from .base import (
    DaemonStatus,
    MANAGED_MARKER_PREFIX,
    SchedulerBackend,
    SpecError,
    is_wsl,
    machine_name,
    render_hint,
    select_backend,
    validate_spec,
)
from .reconcile import ReconcilePlan, compute_plan

__all__ = [
    "DaemonStatus",
    "MANAGED_MARKER_PREFIX",
    "SchedulerBackend",
    "SpecError",
    "ReconcilePlan",
    "compute_plan",
    "is_wsl",
    "machine_name",
    "render_hint",
    "select_backend",
    "validate_spec",
]
