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
  * Age-only, NOT DB-aware: purely ``mtime``-based; not coupled to the
    ``agent_contract_handoffs`` table. A draft is deletable iff it has not
    been touched in ``max_days`` days.
  * Best-effort: every per-file failure is swallowed; a failure NEVER blocks
    session start (same posture as ``db_backup`` and
    ``cleanup._prune_old_files``).

Threshold: ``GAIA_CONTRACT_DRAFTS_MAX_DAYS`` (default 7 days).
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Default retention: a draft untouched for this many days is prunable.
DEFAULT_MAX_DAYS = 7

# Environment override for the retention threshold.
MAX_DAYS_ENV = "GAIA_CONTRACT_DRAFTS_MAX_DAYS"


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


def gc_contract_drafts(max_days: Optional[int] = None) -> int:
    """Delete contract-draft JSON files older than the retention threshold.

    Args:
        max_days: Override the retention threshold (used by tests). When None,
            resolved from ``GAIA_CONTRACT_DRAFTS_MAX_DAYS`` (default 7).

    Returns:
        The number of draft files deleted (0 when nothing matched, the dir is
        absent, or a non-fatal failure occurred).

    Never raises -- every failure path logs at debug and returns so the caller
    (``session_start.py``) is never blocked.
    """
    try:
        from gaia.contract.drafts import drafts_dir
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        # hooks/modules/session/contract_drafts_gc.py -> repo root is 4 up.
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo) not in _sys.path:
            _sys.path.insert(0, str(_repo))
        try:
            from gaia.contract.drafts import drafts_dir
        except ImportError as exc:
            logger.debug(
                "contract_drafts_gc: gaia.contract.drafts unavailable "
                "(non-fatal): %s", exc,
            )
            return 0

    days = _resolve_max_days() if max_days is None else max_days

    try:
        directory = drafts_dir()
    except Exception as exc:  # noqa: BLE001 -- must never block session start
        logger.debug("contract_drafts_gc: drafts_dir() failed (non-fatal): %s", exc)
        return 0

    cutoff = time.time() - days * 86400
    deleted = 0

    try:
        entries = list(directory.glob("*.json"))
    except OSError as exc:
        logger.debug("contract_drafts_gc: glob failed (non-fatal): %s", exc)
        return 0

    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                deleted += 1
        except OSError as exc:
            # Best-effort: a single unreadable/locked/racing file must not
            # abort the sweep or block session start.
            logger.debug(
                "contract_drafts_gc: skipping %s (non-fatal): %s", entry, exc
            )
            continue

    if deleted:
        logger.info(
            "contract_drafts_gc: pruned %d draft(s) older than %d day(s)",
            deleted, days,
        )
    return deleted
