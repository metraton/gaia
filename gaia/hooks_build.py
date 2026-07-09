"""
gaia.hooks_build -- content identity of a wired/running Gaia hooks tree.

The problem this solves: `gaia dev` content-addresses the packed tarball
(``jaguilar87-gaia-<ver>+<sha8>.tgz``, see ``bin/cli/_pack_helpers.content_hash8``)
but never bumps the tarball's INTERNAL ``package.json`` version. So two
genuinely different dev builds ship the SAME semver (e.g. 5.1.3). Any
freshness signal keyed on semver alone is blind to that drift.

``hooks_content_hash`` closes that gap by producing a deterministic digest of
the hooks tree's actual bytes, so two same-version builds with different code
produce different digests. It is the directory-tree analogue of
``_pack_helpers.content_hash8`` (same SHA-256 / first-8-hex convention),
extended from one file to a whole tree.

Home rationale: this module lives in the ``gaia`` package because that is the
ONE import root reachable from BOTH callers -- the SessionStart hook (which
adds the package root to ``sys.path`` and already imports ``gaia.paths``) and
``bin/cli/doctor.py`` (which inserts the package root before importing
``gaia.store.writer``). ``bin/cli/_pack_helpers`` is NOT reachable from the
hook (``bin/`` is not on the hook's path), so the shared digest cannot live
there without duplication.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# File suffixes that constitute the executable identity of a hooks tree. The
# hooks tree is Python entry points + modules plus the generated hooks.json /
# any config JSON; compiled artefacts (``.pyc`` under ``__pycache__``) are
# derived, not source, and are excluded so a stale bytecode cache never
# perturbs the digest.
_HASHED_SUFFIXES = frozenset({".py", ".json"})


def _sha256_file(path: Path) -> str:
    """Full SHA-256 hex of *path*'s bytes, streamed in 64 KiB chunks.

    Mirrors ``_pack_helpers.content_hash8``'s streaming read (that helper
    truncates to 8 hex for a single file; here the per-file full digest feeds
    an outer aggregate that is itself truncated to 8 hex).
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hooks_content_hash(hooks_dir: Path) -> str:
    """Return an 8-hex content digest of the hooks tree rooted at *hooks_dir*.

    Deterministic across processes and machines: the aggregate SHA-256 folds
    each ``*.py`` / ``*.json`` file's POSIX-relative path and full SHA-256, in
    sorted-path order, so the result depends only on the tree's content and
    layout -- not on filesystem iteration order or absolute location. The first
    8 hex chars are returned, matching ``content_hash8``'s convention.

    Returns ``""`` when *hooks_dir* is not a directory (a missing/unresolvable
    tree is the caller's signal to degrade, never a false match: ``""`` never
    equals a real digest).
    """
    hooks_dir = Path(hooks_dir)
    if not hooks_dir.is_dir():
        return ""

    files = sorted(
        p
        for p in hooks_dir.rglob("*")
        if p.is_file() and p.suffix in _HASHED_SUFFIXES
    )

    agg = hashlib.sha256()
    for p in files:
        rel = p.relative_to(hooks_dir).as_posix()
        agg.update(rel.encode("utf-8"))
        agg.update(b"\0")
        agg.update(_sha256_file(p).encode("ascii"))
        agg.update(b"\n")
    return agg.hexdigest()[:8]


__all__ = ["hooks_content_hash"]
