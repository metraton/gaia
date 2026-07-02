"""
SessionStart DB auto-backup (AC-7).

SessionStart fires MANY times per day (every new session, every SDK
invocation), so a snapshot-on-every-launch would flood ~/.gaia/snapshots.
This module throttles the auto-backup to at most once per 24h: it creates a
gzip snapshot of ~/.gaia/gaia.db ONLY when the newest existing snapshot is
older than 24h (or none exists yet), then enforces retention (keep the last
5 snapshots).

Design invariants (shared with `gaia uninstall`, see AC-6):
  * COPY-based: the snapshot streams the live DB through gzip into a NEW
    file; the source DB is never moved, renamed, deleted, or opened for
    writing. A concurrent writer can at worst yield a torn read in the
    snapshot; it can never corrupt or lose the source.
  * ONE shared implementation: the actual "create gzip snapshot + rotate"
    logic lives in gaia.paths.snapshot (create_snapshot / enforce_retention /
    latest_snapshot_age_seconds). This module only adds the 24h throttle and
    the non-fatal wrapper. bin/cli/uninstall.py calls the same helper.
  * Non-fatal: like cleanup_expired_grants / expire_db_pendings in
    session_start.py, any failure here logs at debug and returns; it must
    NEVER block session start.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 24h throttle window (seconds). SessionStart can fire many times a day; we
# snapshot at most once per this window.
THROTTLE_SECONDS = 24 * 60 * 60

# Retention: keep the newest N snapshots across the shared snapshot pool.
RETAIN = 5


def maybe_backup_db(force: bool = False) -> Optional[str]:
    """Create a throttled gzip snapshot of gaia.db at SessionStart.

    Snapshots only when the newest existing snapshot is older than
    THROTTLE_SECONDS (or none exists). After a successful snapshot, retention
    trims the pool to the newest RETAIN files.

    Args:
        force: Bypass the 24h throttle (used by tests / manual triggers).
            The DB-existence check and retention still apply.

    Returns:
        The path of the snapshot that was created, or None when nothing was
        written (throttled, DB absent, or a non-fatal failure occurred).

    Never raises -- every failure path logs at debug and returns None so the
    caller (session_start.py) is never blocked.
    """
    try:
        from gaia.paths import (
            create_snapshot,
            db_path,
            latest_snapshot_age_seconds,
            snapshot_dir,
        )
    except ImportError:
        import pathlib as _pl
        import sys as _sys
        # hooks/modules/session/db_backup.py -> repo root is 4 parents up.
        _repo = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo) not in _sys.path:
            _sys.path.insert(0, str(_repo))
        try:
            from gaia.paths import (
                create_snapshot,
                db_path,
                latest_snapshot_age_seconds,
                snapshot_dir,
            )
        except ImportError as exc:
            logger.debug("db_backup: gaia.paths unavailable (non-fatal): %s", exc)
            return None

    try:
        db = db_path()
        snap_dir = snapshot_dir()

        if not db.exists():
            logger.debug("db_backup: DB %s absent; nothing to snapshot", db)
            return None

        # Throttle: skip when a snapshot younger than the window already exists.
        if not force:
            age = latest_snapshot_age_seconds(snap_dir)
            if age is not None and age < THROTTLE_SECONDS:
                logger.debug(
                    "db_backup: newest snapshot is %.0fs old (< %ds throttle); "
                    "skipping SessionStart backup",
                    age, THROTTLE_SECONDS,
                )
                return None

        result = create_snapshot(
            db, snap_dir, retain=RETAIN, prefix="sessionstart",
        )

        if result.get("error"):
            logger.debug(
                "db_backup: snapshot failed (non-fatal): %s", result["error"]
            )
            return None
        if not result.get("created"):
            return None

        pruned = result.get("pruned") or []
        logger.info(
            "db_backup: SessionStart snapshot created at %s (pruned %d old)",
            result.get("path"), len(pruned),
        )
        return result.get("path")
    except Exception as exc:  # noqa: BLE001 -- must never block session start
        logger.debug("db_backup: unexpected failure (non-fatal): %s", exc)
        return None
