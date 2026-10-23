"""
gaia.paths.backup -- verified, compressed, bounded copies of gaia.db.

Every backup of the live database goes through write_verified_gzip; a copy that
fails verification is deleted and never replaces or prunes an existing backup.
Retention reads the timestamp in the file name, never the clock. The last
session-start outcome is kept in the state dir, not snapshots/, which may be what failed.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

# Measured on a 626 MB synthetic DB: integrity_check 0.86 s vs quick_check 0.37 s,
# so the stronger check is kept; gzip level 6 took 5.3 s vs 28.5 s at level 9 for 11% more bytes.
CHECK = "integrity_check"
COMPRESS_LEVEL = 6
STATUS_FILE = "db-backup-status.json"
STALE_AFTER_SECONDS = 48 * 3600
CLOCK_SKEW_TOLERANCE_SECONDS = 300
PARTIAL_STALE_SECONDS = 2 * 3600
GFS_EXEMPT_PREFIXES = ("uninstall",)

_SUFFIX = ".db.gz"
_PARTIAL_SUFFIXES = (".copy", ".part")
_STAMP = re.compile(r"(\d{8}T\d{6})(\d{6})?")


class BackupVerificationError(Exception):
    """The copy failed verification and was discarded."""


def _copy(source: sqlite3.Connection, dest_path: Path) -> None:
    dest = sqlite3.connect(dest_path)
    try:
        source.backup(dest)
    finally:
        dest.close()


def _verify(dest_path: Path, accept: Optional[Callable[[sqlite3.Connection], Optional[str]]]) -> Optional[str]:
    con = sqlite3.connect(dest_path)
    try:
        rows = [r[0] for r in con.execute(f"PRAGMA {CHECK}").fetchmany(5)]
        if rows != ["ok"]:
            return f"{CHECK}: {'; '.join(rows)}"
        return accept(con) if accept else None
    except sqlite3.DatabaseError as exc:
        return f"{CHECK}: {exc}"
    finally:
        con.close()


def _compress(src_path: Path, dst_path: Path) -> None:
    with open(src_path, "rb") as src, open(dst_path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=COMPRESS_LEVEL) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        raw.flush()
        os.fsync(raw.fileno())


def _partial(target: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=suffix)
    os.close(fd)
    return Path(name)


def write_verified_gzip(
    source: sqlite3.Connection,
    target: Path,
    *,
    accept: Optional[Callable[[sqlite3.Connection], Optional[str]]] = None,
) -> dict:
    """Publish a verified gzip copy of ``source`` at ``target``, or raise and leave nothing.

    ``accept`` may add a check on the uncompressed copy; it returns None to accept
    or a reason to reject. Raises BackupVerificationError when a check fails and
    OSError when the copy cannot be written.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    copy = _partial(target, ".copy")
    part = None
    seconds: Dict[str, float] = {}
    try:
        started = time.monotonic()
        _copy(source, copy)
        seconds["copy"] = time.monotonic() - started

        started = time.monotonic()
        problem = _verify(copy, accept)
        seconds["verify"] = time.monotonic() - started
        if problem:
            raise BackupVerificationError(problem)

        part = _partial(target, ".part")
        started = time.monotonic()
        _compress(copy, part)
        seconds["compress"] = time.monotonic() - started
        os.replace(part, target)
    finally:
        for leftover in (copy, Path(f"{copy}-journal"), part):
            if leftover is not None:
                leftover.unlink(missing_ok=True)
    return {
        "path": str(target),
        "size": target.stat().st_size,
        "check": CHECK,
        "verification": "ok",
        "seconds": {k: round(v, 3) for k, v in seconds.items()},
    }


def restore_gzip(backup: Path, dest: Path) -> None:
    """Decompress ``backup`` into ``dest`` (the caller stages and swaps it into place)."""
    with gzip.open(backup, "rb") as src, open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


def snapshot_stamp(path: Path) -> Optional[datetime]:
    """Return the timestamp written into a backup's file name, or None when it has none."""
    match = _STAMP.search(path.name)
    if not match:
        return None
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
    return stamp.replace(microsecond=int(match.group(2) or 0))


def gfs_keep(
    stamps: Dict[Path, datetime], daily: int = 7, weekly: int = 4, monthly: int = 3
) -> Set[Path]:
    """Return the backups grandfather-father-son retention keeps.

    The newest backup of each of the ``daily`` most recent days, ``weekly`` most
    recent ISO weeks and ``monthly`` most recent months that hold a backup.
    """
    newest_first = sorted(stamps, key=stamps.__getitem__, reverse=True)
    tiers = (
        (daily, lambda d: d.date()),
        (weekly, lambda d: tuple(d.isocalendar())[:2]),
        (monthly, lambda d: (d.year, d.month)),
    )
    keep: Set[Path] = set()
    for count, bucket in tiers:
        seen: set = set()
        for path in newest_first:
            key = bucket(stamps[path])
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(key)
            keep.add(path)
    return keep


def _prefix(path: Path) -> str:
    return path.name[: -len(_SUFFIX)].rpartition("-")[0]


def _remove(paths: List[Path]) -> List[str]:
    removed = []
    for path in paths:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    return removed


def prune_gfs(snapshot_dir: Path, now: Optional[float] = None) -> List[str]:
    """Delete snapshots GFS does not keep, plus abandoned partial files; return what was deleted."""
    now = time.time() if now is None else now
    stamps = {}
    for path in snapshot_dir.glob(f"*{_SUFFIX}"):
        stamp = snapshot_stamp(path)
        if stamp is not None and _prefix(path) not in GFS_EXEMPT_PREFIXES:
            stamps[path] = stamp
    kept = gfs_keep(stamps)
    doomed = [p for p in stamps if p not in kept]
    for path in snapshot_dir.glob(".*"):
        if path.name.endswith(_PARTIAL_SUFFIXES) and now - path.stat().st_mtime > PARTIAL_STALE_SECONDS:
            doomed.append(path)
    return _remove(doomed)


def prune_newest(directory: Path, pattern: str, keep: int) -> List[str]:
    """Keep the ``keep`` newest files matching ``pattern`` by name timestamp; delete the rest."""
    dated = [(snapshot_stamp(p), p) for p in directory.glob(pattern)]
    ordered = [p for stamp, p in sorted((d for d in dated if d[0] is not None), reverse=True)]
    return _remove(ordered[keep:])


def write_status(path: Path, status: dict) -> None:
    """Atomically replace the status file with ``status``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staged.write_text(json.dumps(status, indent=2), encoding="utf-8")
    os.replace(staged, path)


def read_status(path: Path) -> dict:
    """Return the recorded status, or {} when there is none or it cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _size(num_bytes: int) -> str:
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def backup_health(snapshot_dir: Path, status_path: Path, now: Optional[float] = None) -> dict:
    """Describe the newest session backup and the last run; warn past 48h or after a failure.

    A snapshot dated further in the future than the clock-skew tolerance is not
    counted as the newest, so a skewed clock cannot make a stale backup look fresh.
    """
    now = time.time() if now is None else now
    status = read_status(status_path)
    verification = status.get("verification", "unknown")
    dated = []
    if snapshot_dir.is_dir():
        for path in snapshot_dir.glob(f"*{_SUFFIX}"):
            if _prefix(path) in GFS_EXEMPT_PREFIXES:
                continue
            stat = path.stat()
            if stat.st_mtime <= now + CLOCK_SKEW_TOLERANCE_SECONDS:
                dated.append((stat.st_mtime, stat.st_size, path))
    health = {"newest": None, "age_seconds": None, "size": None, "verification": verification}
    problems = []
    if dated:
        mtime, size, path = max(dated)
        health.update(newest=str(path), age_seconds=now - mtime, size=size)
        verdict = "verified ok" if verification == "ok" else f"verification {verification}"
        detail = f"newest {path.name}: {health['age_seconds'] / 3600:.1f}h old, {_size(size)}, {verdict}"
        if health["age_seconds"] > STALE_AFTER_SECONDS:
            problems.append("older than 48h")
    else:
        detail = f"no backup in {snapshot_dir}"
        problems.append("no backup yet")
    if status.get("result") == "failed":
        problems.append(f"last backup failed: {status.get('error') or verification}")
    health["severity"] = "warn" if problems else "pass"
    health["detail"] = "; ".join([detail, *problems])
    return health
