"""
gaia.retention.infra -- generic infrastructure shared by every retention rule
in this package, owned by neither ``fs_rules`` (state-based FS retention) nor
``liveness`` (session liveness) because it is not retention LOGIC: a
strictly read-only connection to gaia.db, and the grace-window threshold
applied before any entry is collectible.

Extracted from ``fs_rules.py`` to break a one-way import cycle: ``liveness.py``
needed these two symbols from ``fs_rules.py`` at module level, while
``fs_rules.py`` could only reach ``liveness.session_dead_past_grace`` through
an import deferred to call time -- the deferral masked the cycle rather than
avoiding it. Both retention modules now depend on this module instead of on
each other for infrastructure, so the composite liveness predicate imports
cleanly at module level in ``fs_rules.py``.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

# Grace window applied on top of "owner already closed" before an entry is
# collectible -- the same purpose as gaia.contract.drafts's spent-grace hours:
# a turn that closed moments ago may still be the thing an orchestrator or a
# retry is actively reading out of scratch/tmp/cache.
DEFAULT_GRACE_HOURS = 24
GRACE_HOURS_ENV = "GAIA_FS_RETENTION_GRACE_HOURS"


def _resolve_env_int(name: str, default: int) -> int:
    """Read a non-negative integer threshold from the environment.

    Read on every call (never cached) so a monkeypatched env is honored, and
    a missing/non-integer/negative value falls back to ``default`` -- a
    malformed override must never widen retention silently. Mirrors
    ``gaia.contract.drafts._resolve_env_int``.
    """
    raw = os.environ.get(name, "")
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    return default


def resolve_grace_hours() -> int:
    """The grace window in effect, honoring ``GAIA_FS_RETENTION_GRACE_HOURS``."""
    return _resolve_env_int(GRACE_HOURS_ENV, DEFAULT_GRACE_HOURS)


def _ro_db_connect():
    """Open a strictly read-only, never-create connection to gaia.db.

    Deliberately not ``gaia.store.reader``'s connector, which lazily
    bootstraps the schema -- a retention preview or sweep must be able to run
    against a machine with no DB at all and simply learn nothing. Returns
    None on any failure (absent DB, locked file, missing driver); every
    caller treats None as "no evidence" and acts accordingly.
    """
    try:
        from gaia.paths import db_path

        path = db_path()
        if not Path(path).is_file():
            return None
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return None
