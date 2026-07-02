"""
gaia.paths.snapshot -- shared DB snapshot + retention helper.

Single implementation of "gzip snapshot of gaia.db, then enforce retention"
used by BOTH:
  * ``gaia uninstall`` (backup-by-default, AC-6) -- bin/cli/uninstall.py
  * the SessionStart hook auto-backup (throttled daily, AC-7) --
    hooks/modules/session/db_backup.py

Keeping ONE implementation means the safety guarantees below cannot drift
between the two call sites:

  * COPY-based, never move/rename the source. The live DB is opened
    read-only and its bytes are streamed through gzip into a NEW file;
    there is no code path here that unlinks, renames, or opens
    ``db_path`` for writing. A concurrent writer to the live DB can at
    worst produce a snapshot with a torn read (same risk as ``cp``); it
    can never corrupt or lose the source.
  * Snapshot filenames are timestamp-sortable (``<prefix>-YYYYmmddTHHMMSSffffff
    .db.gz``, microsecond precision), so retention can always find the
    oldest file with a plain lexical sort -- no filename parsing needed.
  * Retention is enforced immediately after every successful snapshot:
    only the newest ``retain`` files (across ALL prefixes, in one shared
    directory) survive.

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
from typing import List, Optional

DEFAULT_RETAIN = 5

# Matches snapshots written by ANY caller (uninstall's "uninstall-*" prefix,
# SessionStart's "sessionstart-*" prefix, or any future one) -- retention is
# a single shared pool across the whole snapshot_dir(), not per-prefix.
SNAPSHOT_GLOB = "*.db.gz"


def _timestamp() -> str:
    """Return a sortable, collision-resistant timestamp for a filename.

    Microsecond precision avoids two snapshots requested within the same
    second (e.g. two rapid SessionStart events in a test) colliding on the
    same filename and one silently overwriting the other.
    """
    return datetime.now().strftime("%Y%m%dT%H%M%S%f")


def create_snapshot(
    db_path: Path,
    snapshot_dir: Path,
    *,
    dry_run: bool = False,
    retain: int = DEFAULT_RETAIN,
    prefix: str = "gaia",
) -> dict:
    """Create a gzip snapshot of ``db_path`` inside ``snapshot_dir``, then
    enforce retention (keep only the newest ``retain`` snapshots).

    This function NEVER touches ``db_path`` beyond opening it for reading --
    no delete, no rename, no write. The only mutations are: (1) writing a
    new ``.db.gz`` file, and (2) deleting the oldest excess snapshots inside
    ``snapshot_dir`` when retention trims them.

    Returns a result dict:
      {"requested": True,
       "source":    "<db path>",
       "path":      "<snapshot path>" (None if the DB does not exist),
       "created":   True/False,
       "dry_run":   True/False,
       "pruned":    ["<path>", ...],   # snapshots deleted by retention
       "error":     "<message>"}       # only present on failure

    Args:
        db_path: Path to the live SQLite DB to snapshot.
        snapshot_dir: Directory the gzip snapshot is written into.
        dry_run: When True, report what would happen without writing or
            deleting anything.
        retain: How many snapshots to keep in ``snapshot_dir`` after this
            call (applies across all prefixes, not just this call's).
        prefix: Filename prefix identifying the caller (e.g. "uninstall",
            "sessionstart"). Purely informational -- retention treats every
            ``*.db.gz`` file in the directory as one shared pool.
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

    snapshot_path = snapshot_dir / f"{prefix}-{_timestamp()}.db.gz"
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
    """Keep only the newest ``retain`` snapshots in ``snapshot_dir``; delete
    the rest.

    Filenames are timestamp-sortable by construction (see ``_timestamp``),
    so a plain lexical sort on the filename reliably orders oldest-to-newest
    without parsing the embedded timestamp.

    Returns the list of deleted paths (empty if nothing was pruned).
    """
    if retain < 0 or not snapshot_dir.exists():
        return []

    files = sorted(snapshot_dir.glob(SNAPSHOT_GLOB), key=lambda p: p.name)
    excess = files[: max(0, len(files) - retain)]

    pruned: List[str] = []
    for f in excess:
        try:
            f.unlink()
            pruned.append(str(f))
        except OSError:
            # Best-effort: a file removed by a racing process, or a
            # permissions hiccup, is not fatal to the caller.
            continue
    return pruned


def latest_snapshot_age_seconds(snapshot_dir: Path) -> Optional[float]:
    """Return the age in seconds of the most recent snapshot in
    ``snapshot_dir``, or None if the directory does not exist or has no
    snapshots yet.

    "Most recent" is determined by the timestamp-sortable filename (the
    max by name), not by filesystem mtime, so this is stable even if the
    file's mtime were altered by a copy/restore.
    """
    if not snapshot_dir.exists():
        return None

    files = list(snapshot_dir.glob(SNAPSHOT_GLOB))
    if not files:
        return None

    newest = max(files, key=lambda p: p.name)
    return time.time() - newest.stat().st_mtime
