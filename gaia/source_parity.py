"""gaia.source_parity -- does the INSTALLED package still match the SOURCE tree?

Gaia's freshness signals form a chain: source checkout -> installed package ->
wired ``.claude/hooks`` -> the hooks a running session pinned at start. Two
links were already diagnosed (``check_symlinks_freshness``,
``check_hooks_active_fresh``); the first one was not, so a package built from a
half-saved working tree ran indefinitely while the diagnostic reported perfect
health. This module supplies that first link.

What is compared is decided by the package itself: npm's ``files`` field is the
declaration of what ships, so its directory entries ARE the surface a divergence
can hide in. Deriving the list instead of restating it means adding a tree to
``files`` extends this check with no second list to keep in sync.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Present in a checkout, absent from every packed artifact (``build/`` is not in
# npm's ``files``), which is what makes it a decisive source-tree marker.
SOURCE_MARKER = "build/gaia.manifest.json"

# Suffixes carrying executable identity anywhere: interpreted code plus the
# JSON that wires it. Markdown is code only where a prompt IS the artifact, so
# it is compared under _PROMPT_TREES and skipped elsewhere -- a drifted
# hooks/README.md changes nothing that runs.
_CODE_SUFFIXES = frozenset({".py", ".json", ".sh", ".js", ".mjs"})
_PROMPT_TREES = frozenset({"agents", "skills"})

# Derived or vendored, never authored: comparing them reports churn that no
# commit can explain and no repack can settle.
_SKIP_DIRS = frozenset({"__pycache__", "node_modules", ".git", ".pytest_cache", ".venv"})

MISSING = "missing from the install"
EXTRA = "stale, absent from source"
DIFFERS = "differs"


def is_source_checkout(root: Path) -> bool:
    """Whether *root* is a Gaia source checkout rather than an installed copy."""
    return (Path(root) / SOURCE_MARKER).is_file()


def shipped_trees(source_root: Path) -> "list[str] | None":
    """Directory entries of the source package.json's ``files`` field.

    Returns None when package.json is unreadable or declares no ``files``.
    A caller must degrade on None rather than treat it as "nothing to compare":
    an empty comparison would pass vacuously, which is the exact failure this
    module exists to remove.
    """
    try:
        declared = json.loads((Path(source_root) / "package.json").read_text())["files"]
    except Exception:
        return None
    if not isinstance(declared, list):
        return None
    trees = [entry.rstrip("/") for entry in declared if isinstance(entry, str) and entry.endswith("/")]
    return trees or None


def _relevant(tree: str, rel: Path) -> bool:
    if _SKIP_DIRS & set(rel.parts):
        return False
    if rel.suffix in _CODE_SUFFIXES:
        return True
    return rel.suffix == ".md" and tree in _PROMPT_TREES


def _listing(root: Path, tree: str) -> "dict[str, Path]":
    base = Path(root) / tree
    if not base.is_dir():
        return {}
    found = {}
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if _relevant(tree, rel):
            found[rel.as_posix()] = path
    return found


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(source_root: Path, installed_root: Path, trees: "list[str]") -> dict:
    """Compare every shipped file under *trees* between the two roots.

    Returns ``{"compared": int, "divergent": [(path, reason), ...]}`` with
    divergences sorted by path, each ``path`` prefixed by its tree so it reads
    as a repo-relative coordinate.
    """
    compared = 0
    divergent = []
    for tree in trees:
        source, installed = _listing(source_root, tree), _listing(installed_root, tree)
        for rel in sorted(set(source) | set(installed)):
            compared += 1
            coordinate = f"{tree}/{rel}"
            if rel not in installed:
                divergent.append((coordinate, MISSING))
            elif rel not in source:
                divergent.append((coordinate, EXTRA))
            elif _digest(source[rel]) != _digest(installed[rel]):
                divergent.append((coordinate, DIFFERS))
    return {"compared": compared, "divergent": sorted(divergent)}


__all__ = ["SOURCE_MARKER", "compare", "is_source_checkout", "shipped_trees"]
