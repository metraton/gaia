"""
SessionStart backup of gaia.db: one verified, compressed snapshot a day, off the session's path.

maybe_backup_db() runs in the session and only decides: it never opens the DB, and
a due backup is handed to a detached child (this file run with ``--run``), so a slow
copy, a full disk or a corrupt copy can neither delay nor fail session start.
The child, run_backup(), takes a lock so two sessions starting together back up
once, re-checks the throttle under it, writes through gaia.paths.backup, prunes
snapshots/ by grandfather-father-son, and records its outcome in the state dir,
where ``gaia doctor`` reads it.
"""

import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

THROTTLE_SECONDS = 24 * 60 * 60
PREFIX = "sessionstart"
LOCK_NAME = ".sessionstart.lock"
# Longer than any measured run (a 600 MB DB takes well under a minute), so only
# a lock whose owner died is reclaimed.
LOCK_STALE_SECONDS = 2 * 60 * 60


def _load():
    """Import gaia.paths, adding the package root when the hooks tree runs without it on sys.path."""
    try:
        from gaia.paths import backup, db_path, snapshot_dir, state_dir
    except ImportError:
        repo = Path(__file__).resolve().parent.parent.parent.parent
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from gaia.paths import backup, db_path, snapshot_dir, state_dir
    return backup, db_path(), snapshot_dir(), state_dir() / backup.STATUS_FILE


def _backup_due(snap_dir: Path, now: float, skew_tolerance: float) -> bool:
    """Due when no session snapshot is younger than the throttle; future-dated ones do not count."""
    if not snap_dir.is_dir():
        return True
    ages = [now - p.stat().st_mtime for p in snap_dir.glob(f"{PREFIX}-*.db.gz")]
    recent = [age for age in ages if age >= -skew_tolerance]
    return not recent or min(recent) >= THROTTLE_SECONDS


def _record(backup, status_path: Path, status: dict) -> dict:
    status["at"] = time.time()
    try:
        backup.write_status(status_path, status)
    except OSError as exc:
        logger.warning("db_backup: could not record status at %s: %s", status_path, exc)
    if status.get("result") == "failed":
        logger.warning("db_backup: SessionStart backup failed: %s", status.get("error"))
    return status


def _acquire(lock: Path) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < LOCK_STALE_SECONDS:
                return False
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return True
    return False


def run_backup(force: bool = False) -> dict:
    """Take the session backup now, unless throttled or another run holds the lock.

    Returns the run's status; ``result`` is created, throttled, locked, absent or
    failed. A failure is recorded in the status file, never raised.
    """
    backup, db, snap_dir, status_path = _load()
    if not db.exists():
        return {"result": "absent"}
    lock = snap_dir / LOCK_NAME
    try:
        if not _acquire(lock):
            return {"result": "locked"}
    except OSError as exc:
        return _record(backup, status_path, {
            "result": "failed", "verification": "not reached", "error": f"snapshots dir unusable: {exc}",
        })
    try:
        if not force and not _backup_due(snap_dir, time.time(), backup.CLOCK_SKEW_TOLERANCE_SECONDS):
            return {"result": "throttled"}
        target = snap_dir / f"{PREFIX}-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.db.gz"
        try:
            source = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=30)
            try:
                written = backup.write_verified_gzip(source, target)
            finally:
                source.close()
        except backup.BackupVerificationError as exc:
            return _record(backup, status_path, {
                "result": "failed", "verification": f"failed: {exc}", "error": str(exc),
            })
        except Exception as exc:  # noqa: BLE001 -- recorded for doctor, never raised into the session
            return _record(backup, status_path, {
                "result": "failed", "verification": "not reached", "error": f"{type(exc).__name__}: {exc}",
            })
        pruned = backup.prune_gfs(snap_dir)
        return _record(backup, status_path, {"result": "created", **written, "pruned": len(pruned)})
    finally:
        lock.unlink(missing_ok=True)


def maybe_backup_db(force: bool = False) -> Optional[int]:
    """Start a detached backup when one is due; return its pid, or None when nothing started.

    Never raises and never opens the DB, so session start cannot be blocked or failed by it.
    """
    try:
        backup, db, snap_dir, status_path = _load()
        if not db.exists():
            return None
        if not force and not _backup_due(snap_dir, time.time(), backup.CLOCK_SKEW_TOLERANCE_SECONDS):
            return None
        argv = [sys.executable, "-I", str(Path(__file__).resolve()), *(["--force"] if force else []), "--run"]
        detach = (
            {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, **detach,
        )
        return child.pid
    except Exception as exc:  # noqa: BLE001
        logger.warning("db_backup: could not start the SessionStart backup: %s", exc)
        try:
            _record(backup, status_path, {"result": "failed", "verification": "not reached",
                                          "error": f"could not start: {exc}"})
        except Exception:  # noqa: BLE001 -- _load itself failed; the warning above is all there is
            pass
        return None


if __name__ == "__main__" and "--run" in sys.argv:
    run_backup(force="--force" in sys.argv)
