"""protected_paths.py -- the ONE protected-path predicate, for every write surface.

Gaia's executable hook code is write-protected on two surfaces: the Write/Edit
gate in ``adapters/claude_code.py`` and the Bash command-string guard in
``protected_path_guard.py``. Until this module existed the scope was stated
twice, in prose, with the guard's docstring claiming to mirror the adapter --
and that duplication is what produced the inversion below. Both surfaces now
consume this predicate, so widening one cannot leave the other behind.

THE INVERSION THIS FIXES. The adapter derived its protected root from
``Path(__file__).parent.parent`` -- the directory the RUNNING hook module was
loaded from. While the installed hook directory was a symlink back into the
checkout both resolved identically and the source tree was protected
INCIDENTALLY. Once a dev install materialises the installed copy into a package
store, the source hook tree is no longer under that root, the containment check
raises, and the write passes UNGATED. The scope of a security control was
therefore a function of the deployment layout, protecting the copy that the next
install overwrites and leaving unprotected the only place an edit is durable.

THE DERIVATION. The protected set is a UNION of lanes, none of which reads the
load location of the evaluating module, so materialising the hooks differently
(symlink, package store, plain copy, container mount) changes NEITHER lane:

  * DECLARED IDENTITY -- the checkout paths recorded in the workspace registry
    (``projects.path``), i.e. outside any deployment, narrowed to the rows that
    are Gaia checkouts.
  * STRUCTURAL SHAPE -- a ``hooks`` directory anchored under a harness install
    root (a ``.claude`` component) or under the distributed package directory
    (``@jaguilar87/gaia``). Pure path shape: no filesystem, no database.
  * ROOT MARKER -- a ``hooks`` directory whose parent carries a Gaia package
    marker. This reads the filesystem AROUND THE TARGET, never around this
    module, and covers a plain copy or a container mount that neither of the
    other lanes names.

Resolution fails CLOSED: the identity lane needs a database read, which can
fail inside PreToolUse, and an empty root set from a failed read is the same
inversion by a new route. So a lane that yields nothing only ever declines to
ADD roots -- the structural and marker lanes still fire on their own.

The ``.md`` carve-out (documentation does not execute code) and the
``settings.json`` / ``settings.local.json`` special case keep their existing
behaviour exactly.

Public API:
    is_protected_hook_path(path: str) -> bool
    declared_hook_tree_roots() -> tuple[str, ...]
    reset_caches() -> None
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

# The npm package name Gaia ships under. It identifies a Gaia root in the
# source checkout and in every installed materialisation alike, because the
# same package.json travels into the artifact.
_PACKAGE_NAME = "@jaguilar87/gaia"

# Scoped package directory shape: ``.../@jaguilar87/gaia/hooks/...``. Present in
# any node package store layout (npm, pnpm, yarn) without depending on which.
_PACKAGE_SCOPE_DIR = "@jaguilar87"
_PACKAGE_DIR = "gaia"

# The harness install root. ``.claude/hooks`` is what the host loads, whether it
# is a symlink, a copy, or a mount.
_HARNESS_DIR = ".claude"

_HOOKS_DIR = "hooks"

_SETTINGS_BASENAMES = frozenset({"settings.json", "settings.local.json"})

# Resolved once per process: PreToolUse runs on every tool call, and the
# registry read and the marker stat would otherwise repeat per call.
_declared_roots_cache: Optional[Tuple[str, ...]] = None
_marker_cache: Dict[str, bool] = {}


def reset_caches() -> None:
    """Drop the memoised registry roots and marker verdicts (tests)."""
    global _declared_roots_cache
    _declared_roots_cache = None
    _marker_cache.clear()


def _is_gaia_root(directory: Path) -> bool:
    """Return True iff `directory` is the root of a Gaia checkout or package."""
    key = str(directory)
    cached = _marker_cache.get(key)
    if cached is not None:
        return cached

    verdict = False
    try:
        if (directory / "build" / "gaia.manifest.json").is_file():
            verdict = True
        else:
            package_json = directory / "package.json"
            if package_json.is_file():
                payload = json.loads(package_json.read_text(encoding="utf-8"))
                verdict = payload.get("name") == _PACKAGE_NAME
    except Exception:
        # An unreadable or malformed marker must not decide the verdict alone;
        # the other lanes still apply.
        verdict = False

    _marker_cache[key] = verdict
    return verdict


def _read_registry_hook_roots() -> Tuple[str, ...]:
    """Return ``<checkout>/hooks`` for every Gaia checkout in the registry.

    The registry is the declaration recorded OUTSIDE the deployment, which is
    what makes this lane install-mode invariant. Imports are local because a
    hook adapter that imports the ``gaia`` package at module scope breaks every
    entry point when the package is not on the path.
    """
    import sqlite3

    from gaia.paths import db_path

    database = db_path()
    if not database or not Path(database).exists():
        return ()

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT path FROM projects WHERE path IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()

    roots = []
    for (candidate,) in rows:
        root = Path(candidate)
        if _is_gaia_root(root) and (root / _HOOKS_DIR).is_dir():
            roots.append(str(root / _HOOKS_DIR))
    return tuple(roots)


def declared_hook_tree_roots() -> Tuple[str, ...]:
    """Hook-tree roots declared in the workspace registry, or () if unresolvable."""
    global _declared_roots_cache
    if _declared_roots_cache is None:
        try:
            _declared_roots_cache = _read_registry_hook_roots()
        except Exception:
            # Fail CLOSED: an unresolved identity contributes no roots and
            # never removes any.
            _declared_roots_cache = ()
    return _declared_roots_cache


def _shape_hit(parts: Tuple[str, ...]) -> bool:
    """True iff the path shape places it inside an anchored ``hooks`` tree."""
    for index, part in enumerate(parts):
        remainder = parts[index + 1:]
        if part == _HARNESS_DIR and _HOOKS_DIR in remainder:
            return True
        if (
            part == _PACKAGE_SCOPE_DIR
            and remainder[:1] == (_PACKAGE_DIR,)
            and _HOOKS_DIR in remainder[1:]
        ):
            return True
    return False


def _declared_root_hit(candidate: Path) -> bool:
    roots = declared_hook_tree_roots()
    if not roots:
        return False
    for root in roots:
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _marker_hit(candidate: Path) -> bool:
    """True iff some ``hooks`` ancestor of `candidate` sits in a Gaia root."""
    for ancestor in (candidate,) + tuple(candidate.parents):
        if ancestor.name == _HOOKS_DIR and _is_gaia_root(ancestor.parent):
            return True
    return False


def _candidates(path_str: str) -> Tuple[Path, ...]:
    """The forms a single path argument must be judged in.

    Three, because they disagree in ways that matter. The literal form keeps the
    ``.claude`` component that a symlinked install destroys on resolution; the
    absolute form gives a relative shell token a root to be judged against; the
    resolved form is where a symlink actually lands.
    """
    literal = Path(os.path.normpath(os.path.expanduser(path_str)))
    forms = [literal]

    absolute = Path(os.path.abspath(literal))
    if absolute not in forms:
        forms.append(absolute)

    try:
        resolved = absolute.resolve()
    except Exception:
        resolved = absolute
    if resolved not in forms:
        forms.append(resolved)

    return tuple(forms)


def is_protected_hook_path(path_str: str) -> bool:
    """Return True iff `path_str` names write-protected Gaia configuration.

    Args:
        path_str: A file path, absolute or relative, as a caller wrote it --
            the ``file_path`` parameter of a Write/Edit call or a token lifted
            out of a Bash command string.

    Returns:
        True when the path is inside a Gaia hook tree (any lane, any
        deployment) and is not documentation, or when it is a ``.claude``
        settings file.
    """
    if not path_str:
        return False

    candidates = _candidates(path_str)

    for candidate in candidates:
        if (
            _shape_hit(candidate.parts)
            or _declared_root_hit(candidate)
            or _marker_hit(candidate)
        ):
            # Documentation does not execute code and is exempt.
            return candidate.suffix != ".md"

    for candidate in candidates:
        if candidate.name in _SETTINGS_BASENAMES and _HARNESS_DIR in candidate.parts:
            return True

    return False
