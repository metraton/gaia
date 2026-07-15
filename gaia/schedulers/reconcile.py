"""Reconcile DESIRED state (gaia.db) against the LOCAL scheduler.

This is the shared, READ-ONLY (T0) comparison used by three callers so they
agree on what "drift" means:

  - `gaia schedule status`  -- prints the plan.
  - `gaia schedule sync`    -- applies the plan (the T3 install/remove).
  - the SessionStart hook    -- surfaces a zero-noise "N tasks not installed
                                here" line and never writes anything.

It never mutates the scheduler; it only computes what a sync WOULD do. The
caller decides whether to act (and only `sync` does, under T3 consent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import DaemonStatus, SpecError, machine_name, select_backend


@dataclass
class ReconcilePlan:
    machine: str
    backend: Optional[str]
    missing: List[dict] = field(default_factory=list)      # enabled, desired-here, not installed
    drift: List[dict] = field(default_factory=list)        # installed but cron expr differs
    orphans: List[str] = field(default_factory=list)       # managed here, no longer desired
    disabled_present: List[str] = field(default_factory=list)  # disabled but still installed
    invalid: List[dict] = field(default_factory=list)      # spec that fails to translate
    daemon: Optional[DaemonStatus] = None
    available: bool = True                                  # a usable backend exists here

    @property
    def in_sync(self) -> bool:
        return not (self.missing or self.drift or self.orphans or self.disabled_present)

    @property
    def action_count(self) -> int:
        return len(self.missing) + len(self.drift) + len(self.orphans) + len(self.disabled_present)


def compute_plan(workspace: Optional[str] = None, machine: Optional[str] = None) -> ReconcilePlan:
    """Compute the reconcile plan for ``machine`` (default: this machine).

    Fully read-only and fail-soft: import/errors degrade to an empty,
    available=False plan rather than raising, so the SessionStart hook can call
    this without any risk of blocking session start.
    """
    mach = machine or machine_name()
    plan = ReconcilePlan(machine=mach, backend=None)

    backend = select_backend()
    if backend is None or not backend.available():
        plan.available = False
        return plan
    plan.backend = backend.name

    try:
        from gaia.store.reader import list_scheduled_tasks, scheduled_tasks_for_machine
    except Exception:
        plan.available = False
        return plan

    try:
        entries = backend.managed_entries() if hasattr(backend, "managed_entries") else {
            n: "" for n in backend.list_managed()
        }
    except Exception:
        entries = {}

    # Enabled tasks desired on THIS machine.
    desired = scheduled_tasks_for_machine(mach, workspace=workspace)
    desired_names = {t["name"] for t in desired}

    for task in desired:
        name = task["name"]
        try:
            want_expr = backend.translate(task)
        except SpecError as exc:
            plan.invalid.append({"name": name, "error": str(exc)})
            continue
        except Exception as exc:
            plan.invalid.append({"name": name, "error": repr(exc)})
            continue
        if name not in entries:
            plan.missing.append({"name": name, "expr": want_expr})
        elif entries[name] and entries[name] != want_expr:
            plan.drift.append({"name": name, "want": want_expr, "have": entries[name]})

    # Orphans: managed here but no longer an ENABLED desired task for this
    # machine. Split disabled-but-present out for a clearer message.
    all_tasks = list_scheduled_tasks(workspace=workspace, include_disabled=True)
    disabled_names = {t["name"] for t in all_tasks if not t.get("enabled")}
    for name in entries:
        if name in desired_names:
            continue
        if name in disabled_names:
            plan.disabled_present.append(name)
        else:
            plan.orphans.append(name)

    try:
        plan.daemon = backend.ensure_daemon()
    except Exception:
        plan.daemon = None

    return plan
