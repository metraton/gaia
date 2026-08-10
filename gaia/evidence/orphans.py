"""
gaia.evidence.orphans -- detect on-disk evidence blobs no row references.

A blob under the canonical evidence root (``gaia.paths.resolver.
evidence_dir()``) is orphaned when no ``evidence.artifact_path`` row points
at it. Historically this happened because ``bin/cli/evidence.py::_cmd_add``
wrote the blob to disk BEFORE ``insert_evidence`` validated the deposit --
every rejected deposit left exactly this shape of file behind (see
``tests/cli/test_evidence_deposit_reject_no_orphan.py`` for the fix). That
write-before-validate gap is closed, but closing it does not retroactively
remove the files it already produced -- they are still sitting under the
evidence root, and this sweep exists to find them.

Deliberately undifferentiated: ``find_orphan_blobs()`` reports EVERY blob
file under the evidence root with no referencing row, regardless of how old
it is or when it was written. An age-based or "only new files" filter was
considered and rejected: a future regression that reopens the
write-before-validate gap would produce new orphans indistinguishable from
the historical ones, and a sweep that only looked at new files would mask
that regression instead of catching it.

Fail-closed via the shared ``gaia.retention.infra._ro_db_connect`` (the same
canonical connection ``fs_rules`` and ``liveness`` import instead of each
defining their own): when gaia.db cannot be read (no file, locked, missing
table), ``find_orphan_blobs()`` returns an empty list. Absence of evidence
can only mean "cannot confirm any orphan here," never "every file must be
one" -- the same posture ``gaia.retention.fs_rules._closed_contract_ids``
uses for its own DB-backed checks.

Public API::

    find_orphan_blobs(root=None) -> list[Path]
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Set

from gaia.retention.infra import _ro_db_connect


def _known_artifact_paths() -> Optional[Set[str]]:
    """Every ``artifact_path`` gaia.db currently references, resolved to an
    absolute string -- or None when the DB cannot be consulted at all."""
    con = _ro_db_connect()
    if con is None:
        return None
    try:
        rows = con.execute(
            "SELECT artifact_path FROM evidence WHERE artifact_path IS NOT NULL"
        ).fetchall()
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass
    return {str(Path(r[0]).resolve()) for r in rows if r and r[0]}


def find_orphan_blobs(root: Optional[Path] = None) -> List[Path]:
    """Every blob file under *root* that no ``evidence`` row references.

    Args:
        root: Evidence root to scan. Defaults to the canonical
            ``gaia.paths.resolver.evidence_dir()``; tests pass an isolated
            root via the per-test ``GAIA_DATA_DIR`` fixture instead.

    Returns:
        A list of absolute Paths, one per orphan file. Empty when *root*
        does not exist or gaia.db cannot be read (see module docstring).
    """
    from gaia.paths.resolver import evidence_dir

    base = root if root is not None else evidence_dir()
    if not base.exists():
        return []

    known = _known_artifact_paths()
    if known is None:
        return []

    orphans: List[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if str(path.resolve()) not in known:
            orphans.append(path)
    return orphans
