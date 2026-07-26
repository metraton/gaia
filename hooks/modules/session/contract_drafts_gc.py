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
    decided by ``gaia.contract.drafts.collectable_drafts``, and the two
    thresholds resolve through that same module. This module holds no retention
    constant and reads no retention env var, so there is nothing here to drift.
    ``gaia cleanup``'s ``_prune_contract_drafts`` calls the identical policy
    function, which is what makes the manual dry-run a true preview of this
    sweep. It was not always: the CLI once implemented its own age-only cutoff,
    and on a 383-file corpus the two selected 100 (all ``spent``) against 0 --
    a preview that showed nothing hours before the sweep removed ~100 files.
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
``GAIA_CONTRACT_DRAFTS_GRACE_HOURS`` (default 24 hours) -- both read by the
policy module, not here.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _load_policy():
    """Import the shared retention policy module, or None when unavailable.

    Mirrors the historical two-step import: hooks run from a directory where
    the repo root may not be on ``sys.path`` yet, so a first failure retries
    after inserting it. The MODULE is returned rather than one function so the
    thresholds come from the same place as the criterion -- this module keeps no
    retention constants of its own to drift out of step.
    """
    try:
        import gaia.contract.drafts as policy

        return policy
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        # hooks/modules/session/contract_drafts_gc.py -> repo root is 4 up.
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo) not in _sys.path:
            _sys.path.insert(0, str(_repo))
        try:
            import gaia.contract.drafts as policy

            return policy
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
        max_days: Override the age threshold (used by tests). When None, the
            policy resolves it from ``GAIA_CONTRACT_DRAFTS_MAX_DAYS``
            (default 7) -- this module does not read the env itself.
        grace_hours: Override the spent-draft grace window. When None, the
            policy resolves it from ``GAIA_CONTRACT_DRAFTS_GRACE_HOURS``
            (default 24).
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
    policy = _load_policy()
    if policy is None:
        return 0

    try:
        selected = policy.collectable_drafts(
            max_age_days=max_days, grace_hours=grace_hours
        )
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
        # Report the policy's own reasons rather than restating the thresholds:
        # this module no longer knows them, and the reason is what a reader
        # needs to tell an ordinary spent sweep from the aged backstop firing.
        reasons: dict = {}
        for record in selected:
            key = str(record.get("reason") or "?")
            reasons[key] = reasons.get(key, 0) + 1
        logger.info(
            "contract_drafts_gc: pruned %d draft(s) by reason: %s",
            deleted,
            ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())),
        )
    return deleted
