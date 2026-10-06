"""
SessionStart DB auto-backup (AC-7).

SessionStart fires many times a day, so the gzip snapshot of gaia.db is
throttled to one per 24h, measured against the newest ``sessionstart``
snapshot only. The snapshot and per-prefix retention live in
gaia.paths.snapshot, shared with ``gaia uninstall``; this module adds the
throttle and must never block session start.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

THROTTLE_SECONDS = 24 * 60 * 60
RETAIN = 5
PREFIX = "sessionstart"


def maybe_backup_db(force: bool = False) -> Optional[str]:
    """Create a gzip snapshot of gaia.db unless a ``sessionstart`` snapshot
    younger than THROTTLE_SECONDS exists; retention then keeps the newest
    RETAIN snapshots of each prefix. A throttled call never opens the DB.

    Args:
        force: Bypass the 24h throttle (used by tests / manual triggers).
            The DB-existence check and retention still apply.

    Returns:
        The path of the snapshot that was created, or None when nothing was
        written (throttled, DB absent, or a non-fatal failure occurred).

    Never raises -- every failure path logs at debug and returns None so
    session start is never blocked.
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

        if not force:
            age = latest_snapshot_age_seconds(snap_dir, PREFIX)
            if age is not None and age < THROTTLE_SECONDS:
                logger.debug(
                    "db_backup: newest snapshot is %.0fs old (< %ds throttle); "
                    "skipping SessionStart backup",
                    age, THROTTLE_SECONDS,
                )
                return None

        result = create_snapshot(db, snap_dir, retain=RETAIN, prefix=PREFIX)

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
    except Exception as exc:  # noqa: BLE001
        logger.debug("db_backup: unexpected failure (non-fatal): %s", exc)
        return None
