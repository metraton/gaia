"""
gaia.paths.snapshot -- gzip snapshots of gaia.db with per-prefix retention.

Shared by ``gaia uninstall`` (prefix ``uninstall``) and the SessionStart
auto-backup (prefix ``sessionstart``) so both keep the same guarantees:

  * The source DB is only ever opened for reading; a snapshot is a new file.
  * Names are ``<prefix>-YYYYmmddTHHMMSSffffff.db.gz``. Within one prefix the
    fixed-width timestamp makes name order creation order; across prefixes
    name order means nothing, so retention and age are computed per prefix.

Public API::

    from gaia.paths.snapshot import (
        create_snapshot,
        enforce_retention,
        latest_snapshot_age_seconds,
        DEFAULT_RETAIN,
        SNAPSHOT_GLOB,
    )
"""

from __future__ import annotations

import gzip
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_RETAIN = 5

SNAPSHOT_GLOB = "*.db.gz"
_SNAPSHOT_SUFFIX = ".db.gz"


def _timestamp() -> str:
    """Return a microsecond timestamp, so two snapshots in one second never share a name."""
    return datetime.now().strftime("%Y%m%dT%H%M%S%f")


def _prefix_of(snapshot: Path) -> str:
    """Return the caller prefix of a ``<prefix>-<timestamp>.db.gz`` file."""
    return snapshot.name[: -len(_SNAPSHOT_SUFFIX)].rpartition("-")[0]


def _snapshots_by_prefix(snapshot_dir: Path) -> Dict[str, List[Path]]:
    """Group the snapshots in ``snapshot_dir`` by prefix, each group oldest first."""
    groups: Dict[str, List[Path]] = {}
    for snapshot in sorted(snapshot_dir.glob(SNAPSHOT_GLOB)):
        groups.setdefault(_prefix_of(snapshot), []).append(snapshot)
    return groups


def create_snapshot(
    db_path: Path,
    snapshot_dir: Path,
    *,
    dry_run: bool = False,
    retain: int = DEFAULT_RETAIN,
    prefix: str = "gaia",
) -> dict:
    """Write a gzip copy of ``db_path`` as ``<prefix>-<timestamp>.db.gz`` in
    ``snapshot_dir``, then keep only the newest ``retain`` snapshots of each prefix.

    ``db_path`` is only opened for reading. Returns::

      {"requested": True,
       "source":    "<db path>",
       "path":      "<snapshot path>" (None if the DB does not exist),
       "created":   True/False,
       "dry_run":   True/False,
       "pruned":    ["<path>", ...],   # snapshots deleted by retention
       "error":     "<message>"}       # only present on failure
    """
    result: dict = {
        "requested": True,
        "source": str(db_path),
        "path": None,
        "created": False,
        "dry_run": dry_run,
        "pruned": [],
    }

    if not db_path.exists():
        result["details"] = "DB does not exist; nothing to snapshot"
        return result

    snapshot_path = snapshot_dir / f"{prefix}-{_timestamp()}{_SNAPSHOT_SUFFIX}"
    result["path"] = str(snapshot_path)

    if dry_run:
        result["details"] = "would create snapshot"
        return result

    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        with open(db_path, "rb") as src, gzip.open(snapshot_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except OSError as exc:
        result["error"] = str(exc)
        return result

    result["created"] = True
    result["details"] = f"snapshot {snapshot_path.stat().st_size} bytes"
    result["pruned"] = enforce_retention(snapshot_dir, retain=retain)
    return result


def enforce_retention(snapshot_dir: Path, retain: int = DEFAULT_RETAIN) -> List[str]:
    """Keep the newest ``retain`` snapshots of each prefix in ``snapshot_dir``,
    delete the rest, and return the deleted paths."""
    if retain < 0 or not snapshot_dir.exists():
        return []

    pruned: List[str] = []
    for snapshots in _snapshots_by_prefix(snapshot_dir).values():
        for snapshot in snapshots[: max(0, len(snapshots) - retain)]:
            try:
                snapshot.unlink()
            except OSError:
                continue
            pruned.append(str(snapshot))
    return pruned


def latest_snapshot_age_seconds(snapshot_dir: Path, prefix: str) -> Optional[float]:
    """Return the age in seconds of the newest ``prefix`` snapshot -- chosen by the
    timestamp in its name, aged by its mtime -- or None when there is none."""
    if not snapshot_dir.exists():
        return None

    snapshots = _snapshots_by_prefix(snapshot_dir).get(prefix)
    if not snapshots:
        return None
    return time.time() - snapshots[-1].stat().st_mtime
