"""
SessionStart contract-drafts garbage collection.

The contract-draft substrate (``~/.gaia/contract_drafts/*.json``) holds one
JSON file per agent contract draft. ``gaia contract init/set/add/fill`` build a
draft incrementally, and ``finalize`` writes the terminal ``agent_contract_
handoffs`` DB row -- but finalize NEVER deletes the draft file. Left unbounded,
the directory grows without limit (observed: ~382 accumulated files).

This module prunes draft files older than a threshold at SessionStart.

Design invariants (sibling of ``db_backup.py``):
  * SessionStart-only: runs once per session at launch, never during a turn,
    so it can never race the SubagentStop backstop nor delete a draft an
    in-progress turn is still writing -- a live draft always has a recent
    mtime and is preserved by the age check.
  * Policy lives in ONE place: which drafts are collectable (and why) is
    decided by ``gaia.contract.drafts.collectable_drafts``, shared with the
    ``gaia cleanup`` CLI. This module only executes the deletions that policy
    selects, so the automatic sweep and the manual dry-run can never disagree
    about what would be removed.
  * Best-effort: every per-file failure is swallowed; a failure NEVER blocks
    session start (same posture as ``db_backup`` and
    ``cleanup._prune_old_files``).

Why age alone was not enough: the age-only rule never collected anything in
practice. Drafts accumulate continuously, so at any moment nearly the whole
directory is younger than the threshold -- 481 files had built up while every
one of them sat inside a 7-day window. The DB-aware ``spent`` rule is what
actually reclaims them: a draft whose turn already has a terminal
``agent_contract_handoffs`` row is a spent copy of a durable record. Age
remains as the backstop for drafts that never finalized.

Thresholds: ``GAIA_CONTRACT_DRAFTS_MAX_DAYS`` (default 7 days) and
``GAIA_CONTRACT_DRAFTS_GRACE_HOURS`` (default 24 hours).
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default retention: a draft untouched for this many days is prunable.
DEFAULT_MAX_DAYS = 7

# Environment override for the retention threshold.
MAX_DAYS_ENV = "GAIA_CONTRACT_DRAFTS_MAX_DAYS"

# Default grace window (hours) a SPENT draft must sit untouched before it is
# collectable. Keeps a just-finalized draft readable while an orchestrator
# relays the turn it closed.
DEFAULT_GRACE_HOURS = 24

# Environment override for the spent-draft grace window.
GRACE_HOURS_ENV = "GAIA_CONTRACT_DRAFTS_GRACE_HOURS"


def _resolve_grace_hours() -> int:
    """Resolve the spent-draft grace window in hours (env-overridable).

    Same posture as ``_resolve_max_days``: read on every call, and any missing,
    non-integer, or negative value falls back to the default.
    """
    raw = os.environ.get(GRACE_HOURS_ENV, "")
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return DEFAULT_GRACE_HOURS


def _resolve_max_days() -> int:
    """Resolve the retention threshold in days.

    Reads ``GAIA_CONTRACT_DRAFTS_MAX_DAYS`` on every call (never cached at
    import) so tests that set the env via monkeypatch are honored. A missing,
    non-integer, or negative value falls back to ``DEFAULT_MAX_DAYS``.
    """
    raw = os.environ.get(MAX_DAYS_ENV, "")
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return DEFAULT_MAX_DAYS


def _load_policy():
    """Import the shared retention policy, or None when unavailable.

    Mirrors the historical two-step import: hooks run from a directory where
    the repo root may not be on ``sys.path`` yet, so a first failure retries
    after inserting it.
    """
    try:
        from gaia.contract.drafts import collectable_drafts

        return collectable_drafts
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        # hooks/modules/session/contract_drafts_gc.py -> repo root is 4 up.
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo) not in _sys.path:
            _sys.path.insert(0, str(_repo))
        try:
            from gaia.contract.drafts import collectable_drafts

            return collectable_drafts
        except ImportError as exc:
            logger.debug(
                "contract_drafts_gc: gaia.contract.drafts unavailable "
                "(non-fatal): %s", exc,
            )
            return None


def gc_contract_drafts(
    max_days: Optional[int] = None,
    grace_hours: Optional[int] = None,
    dry_run: bool = False,
) -> int:
    """Delete the contract drafts the shared retention policy selects.

    Args:
        max_days: Override the age threshold (used by tests). When None,
            resolved from ``GAIA_CONTRACT_DRAFTS_MAX_DAYS`` (default 7).
        grace_hours: Override the spent-draft grace window. When None,
            resolved from ``GAIA_CONTRACT_DRAFTS_GRACE_HOURS`` (default 24).
        dry_run: Select and count WITHOUT deleting anything. The selection is
            identical to a real sweep, so a dry run reports exactly what a real
            run would remove.

    Returns:
        The number of draft files deleted -- or, under ``dry_run``, the number
        that WOULD be deleted. 0 when nothing matched, the policy is
        unavailable, or a non-fatal failure occurred.

    Never raises -- every failure path logs at debug and returns so the caller
    (``session_start.py``) is never blocked.
    """
    collectable_drafts = _load_policy()
    if collectable_drafts is None:
        return 0

    days = _resolve_max_days() if max_days is None else max_days
    hours = _resolve_grace_hours() if grace_hours is None else grace_hours

    try:
        selected = collectable_drafts(max_age_days=days, grace_hours=hours)
    except Exception as exc:  # noqa: BLE001 -- must never block session start
        logger.debug("contract_drafts_gc: policy failed (non-fatal): %s", exc)
        return 0

    if dry_run:
        return len(selected)

    deleted = 0
    for record in selected:
        try:
            Path(str(record["path"])).unlink()
            deleted += 1
        except OSError as exc:
            # Best-effort: a single unreadable/locked/racing file must not
            # abort the sweep or block session start.
            logger.debug(
                "contract_drafts_gc: skipping %s (non-fatal): %s",
                record.get("path"), exc,
            )
            continue

    if deleted:
        logger.info(
            "contract_drafts_gc: pruned %d draft(s) "
            "(age > %dd, or spent + quiet > %dh)",
            deleted, days, hours,
        )
    return deleted
