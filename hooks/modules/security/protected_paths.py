"""protected_paths.py -- the ONE protected-path predicate, for every write surface.

Gaia's executable hook code is write-protected on two surfaces: the Write/Edit
gate in ``adapters/claude_code.py`` and the Bash command-string guard in
``protected_path_guard.py``. Both surfaces consume this predicate, so widening
one cannot leave the other behind.

THE BOUNDARY. Protection follows the INSTALLATION, not the repository. A Gaia
checkout living inside a workspace is a project like any other: edited freely,
gated by git -- status, review at commit, the push -- never by a per-file
approval. What stays protected is a LIVE materialization a host actually loads
and executes: the ``.claude`` harness install, or the scoped npm package store
(``@jaguilar87/gaia``).

WHY THE PRIOR RULE PROTECTED THE CHECKOUT INSTEAD, AND WHY THAT WAS WRONG. An
earlier version additionally protected any directory carrying a Gaia root
marker (``build/gaia.manifest.json`` or a matching ``package.json``) or
recorded in the workspace registry -- and that is precisely how a checkout got
swept in: a plain ``git clone`` of this repository carries the identical
marker, and a scanned workspace project is exactly what the registry records.
Neither lane can tell a checkout from an install, because nothing distinguishes
them at that marker. A source edit's durability is already git's job; a
runtime guard adding a per-file consent step on top of it protects a
relationship that holds on exactly one machine -- a Gaia developer's, where a
checkout happens to sit beside that developer's own install -- and nowhere
else, since every other install of Gaia has no checkout at all. Live installs
remain protected because they are what actually executes; the six verbs that
materialize or refresh one (``install``, ``update``, ``uninstall``, ``cleanup``,
``dev``, ``release``) are T3-gated in code, so replacing a live install still
requires consent even though editing the checkout beside it no longer does.

THE DERIVATION. ``_shape_hit`` is the only lane that is install-specific BY
CONSTRUCTION: it matches the literal path components a live install is
required to have -- a ``hooks`` directory anchored under a harness root (a
``.claude`` component) or under the scoped package directory
(``@jaguilar87/gaia``) -- never a marker file or a registry row a checkout
carries identically. It is therefore the sole lane this predicate consults for
protection, evaluated against a path's literal, absolute, and symlink-resolved
forms alike so a symlinked install cannot dodge it by spelling.

``declared_hook_tree_roots()`` still resolves checkout roots from the
workspace registry, for a DIFFERENT caller: ``_get_gaia_agent_names`` in
``adapters/claude_code.py`` uses it to find the agents directory beside a
checkout for agent-name discovery, not to decide what is write-protected.

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
    """Checkout hook-tree roots declared in the workspace registry, or () if
    unresolvable. Consumed only for agent-name discovery
    (``adapters/claude_code.py::_get_gaia_agent_names``); not a protection
    lane -- see the module docstring."""
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


def resolved_write_target(path_str: str) -> str:
    """The single form a write is granted and consented against.

    Separate from :func:`is_protected_hook_path`, which must fire on ANY form
    and so judges all three: a grant binds to one object, or a file reached by
    two spellings is two permissions.

    Resolving at mint does not freeze the link -- a target retargeted afterwards
    resolves elsewhere, fails to match the grant, and is blocked again under a
    new approval_id.

    Depends on :func:`_candidates` ordering its forms literal, absolute,
    resolved; a form appended after the last would become what grants bind to.
    """
    if not path_str:
        return path_str
    return str(_candidates(path_str)[-1])


def is_protected_hook_path(path_str: str) -> bool:
    """Return True iff `path_str` names write-protected Gaia configuration.

    Args:
        path_str: A file path, absolute or relative, as a caller wrote it --
            the ``file_path`` parameter of a Write/Edit call or a token lifted
            out of a Bash command string.

    Returns:
        True when the path is inside a live-install hook tree (``.claude`` or
        the ``@jaguilar87/gaia`` package store, any deployment shape) and is
        not documentation, or when it is a ``.claude`` settings file. A Gaia
        source checkout is never matched by this predicate -- see the module
        docstring.
    """
    if not path_str:
        return False

    candidates = _candidates(path_str)

    for candidate in candidates:
        if _shape_hit(candidate.parts):
            # Documentation does not execute code and is exempt.
            return candidate.suffix != ".md"

    for candidate in candidates:
        if candidate.name in _SETTINGS_BASENAMES and _HARNESS_DIR in candidate.parts:
            return True

    return False
