"""protected_paths.py -- the ONE protected-path predicate, for every write surface.

Gaia's executable hook code is write-protected on two surfaces: the Write/Edit
gate in ``adapters/tool_policy.py`` and the Bash command-string guard in
``protected_path_guard.py``. Both surfaces consume this predicate, so widening
one cannot leave the other behind.

THE BOUNDARY. Protection follows the INSTALLATION, not the repository. A Gaia
checkout living inside a workspace is a project like any other: edited freely,
gated by git -- status, review at commit, the push -- never by a per-file
approval. What stays protected is a LIVE materialization a host actually loads
and executes: the ``.claude`` harness install, or the scoped npm package store
(``@jaguilar87/gaia``).

WHY THE GAIA ROOT MARKER AND THE WORKSPACE REGISTRY DECIDE NOTHING. A plain
``git clone`` of this repository carries the identical marker
(``build/gaia.manifest.json`` or a matching ``package.json``), and a scanned
workspace project is exactly what the registry records, so neither can tell a
checkout from an install. A source edit's durability is git's job. Live
installs are protected because they are what actually executes; the verbs that
materialize or refresh one (``install``, ``update``, ``uninstall``,
``cleanup``, ``dev``, ``release check``) are T3-gated in ``mutative_verbs``, so
replacing a live install still requires consent.

THE DERIVATION. Two lanes, both install-specific by construction and judged on
a path's literal, absolute and symlink-resolved forms. ``_shape_hit`` matches a
``hooks`` tree under ``.claude``. ``_install_root`` protects EVERYTHING inside
an install root -- a directory whose package.json names ``@jaguilar87/gaia``,
holding no ``.git``, from any channel -- because the plain ``gaia`` CLI imports
``bin/`` and ``gaia/`` from there, so a planted module or ``.pth`` is code the
next call runs.

``declared_hook_tree_roots()`` resolves checkout roots from the workspace
registry for a DIFFERENT caller: ``adapters/subagent_stop_core.py`` uses it to
find the agents directory beside a checkout, not to decide what is
write-protected.

The ``.md`` carve-out (documentation does not execute code) covers a ``.claude``
hook tree and ``hooks/`` inside an install root; ``settings.json`` /
``settings.local.json`` under ``.claude`` stay protected.

Public API:
    is_protected_hook_path(path: str) -> bool
    declared_hook_tree_roots() -> tuple[str, ...]
    reset_caches() -> None
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from .canonical_path import canonical_path, native_path
from .sensitive_paths import expand_home

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

_DEV_PLUGIN_DIR = "dev-plugin"

_SETTINGS_BASENAMES = frozenset({"settings.json", "settings.local.json"})
# Long names an 8.3 alias may stand for (``CLAUDE~1``, ``SETTIN~1.JSO``).
_PROTECTED_SHORT_NAMES = (_HARNESS_DIR, "settings.json", "settings.local.json")

# Resolved once per process: PreToolUse runs on every tool call, and the
# registry read and the marker stat would otherwise repeat per call.
_declared_roots_cache: Optional[Tuple[str, ...]] = None
_marker_cache: Dict[str, bool] = {}

_INSTALL_ROOTS_JUDGED: contextvars.ContextVar[bool] = contextvars.ContextVar("install_roots_judged", default=True)


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

    Imports are local because a hook adapter that imports the ``gaia`` package
    at module scope breaks every entry point when the package is not on the
    path.
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
    unresolvable. Consumed only to find the agents directory beside a checkout
    (``adapters/subagent_stop_core.py``); not a protection lane -- see the
    module docstring."""
    global _declared_roots_cache
    if _declared_roots_cache is None:
        try:
            _declared_roots_cache = _read_registry_hook_roots()
        except Exception:
            _declared_roots_cache = ()
    return _declared_roots_cache


def _shape_hit(parts: Tuple[str, ...]) -> bool:
    """True iff the path shape places it inside a ``.claude`` ``hooks`` tree."""
    return any(
        part == _HARNESS_DIR and _HOOKS_DIR in parts[index + 1:]
        for index, part in enumerate(parts)
    )


def _dev_plugin_caches() -> Tuple[Path, ...]:
    try:
        from gaia.paths import cache_dir
    except Exception:
        return ()
    container = Path(os.path.abspath(cache_dir() / _DEV_PLUGIN_DIR))
    return (container, container.resolve())


def _declares_gaia(directory: Path) -> bool:
    """True iff `directory` holds a package.json naming Gaia's package and no ``.git``: a checkout is never an install."""
    try:
        with open(directory / "package.json", encoding="utf-8") as manifest:
            named = json.load(manifest).get("name") == _PACKAGE_NAME
    except (OSError, ValueError, AttributeError):
        return False
    return named and not os.path.lexists(directory / ".git")


def _install_root(path: Path) -> Optional[Path]:
    """The installed Gaia root `path` is or lies inside, or None."""
    parts = path.parts
    for index in range(len(parts) - 1):
        if os.path.normcase(parts[index]) == _PACKAGE_SCOPE_DIR and os.path.normcase(parts[index + 1]) == _PACKAGE_DIR:
            return Path(*parts[:index + 2])
    for container in _dev_plugin_caches():
        if container in path.parents:
            return Path(*parts[:len(container.parts) + 1])
    return next((d for d in (path, *path.parents) if _declares_gaia(d)), None)


@contextlib.contextmanager
def install_roots_ignored() -> Iterator[None]:
    """Judge protection as if no install root existed, to tell a harness write from an install change."""
    token = _INSTALL_ROOTS_JUDGED.set(False)
    try:
        yield
    finally:
        _INSTALL_ROOTS_JUDGED.reset(token)


def _install_root_hit(path: Path) -> bool:
    root = _install_root(path) if _INSTALL_ROOTS_JUDGED.get() else None
    if root is None:
        return False
    inside = path.relative_to(root).parts
    return not (inside[:1] == (_HOOKS_DIR,) and path.suffix == ".md")


def _live_install_containers() -> Tuple[Path, ...]:
    from .mutative_verbs import _HOOK_ROOT, _hook_root_is_install

    if not _hook_root_is_install():
        return ()
    home = Path(os.path.abspath(os.path.expanduser("~")))
    above_home = {home, *home.parents}
    return tuple(p for p in _HOOK_ROOT.parents[:3] if p not in above_home)


def is_install_root_container(path_str: str) -> bool:
    """True iff `path_str` is a package scope, node_modules, plugin cache or Gaia data directory holding an install root."""
    if not _INSTALL_ROOTS_JUDGED.get():
        return False
    path = Path(os.path.abspath(os.path.expanduser(path_str)))
    for container in _dev_plugin_caches():
        if path in (container, container.parent, container.parent.parent):
            return True
    if path in _live_install_containers() or Path(os.path.realpath(path)) in _live_install_containers():
        return True
    if os.path.normcase(path.name) == _PACKAGE_SCOPE_DIR:
        return _declares_gaia(path / _PACKAGE_DIR)
    return _declares_gaia(path / _PACKAGE_SCOPE_DIR / _PACKAGE_DIR)


def is_install_root_path(path_str: str) -> bool:
    """True iff `path_str` is an installed Gaia root or lies inside one, hook docs excepted."""
    if not path_str:
        return False
    return any(_install_root_hit(candidate) for candidate in _candidates(path_str))


def _candidates(path_str: str) -> Tuple[Path, ...]:
    """The forms a single path argument must be judged in.

    Three, because they disagree in ways that matter. The literal form keeps the
    ``.claude`` component that a symlinked install destroys on resolution; the
    absolute form gives a relative shell token a root to be judged against; the
    resolved form is where a symlink actually lands.
    """
    literal = Path(os.path.normpath(os.path.expanduser(native_path(path_str))))
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


def is_read_spool_path(path_str: str, cwd: Optional[str] = None) -> bool:
    """True iff `path_str` lies in the harness read spool, which only the PreToolUse recorder writes, in process.

    A line written there by any tool would return as an unmarked files_checked
    entry, so every surface refuses it for every role, with no signature.
    `$HOME`, `${HOME}` and `~` expand, and a relative path anchors at `cwd`.
    """
    if not path_str:
        return False
    from gaia.contract.read_spool import spool_dir

    spelled = expand_home(path_str)
    if cwd and not os.path.isabs(spelled) and not spelled.startswith("~"):
        spelled = os.path.join(cwd, spelled)
    root = Path(os.path.abspath(spool_dir()))
    roots = {root, root.resolve()}
    return any(
        candidate == r or r in candidate.parents
        for candidate in _candidates(spelled)
        for r in roots
    )


def is_protected_hook_path(path_str: str) -> bool:
    """Return True iff `path_str` names write-protected Gaia configuration.

    Args:
        path_str: A file path, absolute or relative, as a caller wrote it --
            the ``file_path`` parameter of a Write/Edit call or a token lifted
            out of a Bash command string.

    Returns:
        True when the path is inside an installed Gaia root (other than a
        ``hooks/`` ``.md``) or a ``.claude`` hook tree (other than a ``.md``),
        or when it is a ``.claude`` settings file. A Gaia
        source checkout is never matched by this predicate -- see the module
        docstring.
    """
    if not path_str:
        return False

    # The canonical form is judged as one more spelling, never bound to: a
    # grant names the resolved file (resolved_write_target).
    candidates = _candidates(path_str) + (
        Path(canonical_path(path_str, _PROTECTED_SHORT_NAMES)),
    )

    for candidate in candidates:
        if _shape_hit(candidate.parts):
            # Documentation does not execute code and is exempt.
            return candidate.suffix != ".md"

    if any(_install_root_hit(candidate) for candidate in candidates):
        return True

    for candidate in candidates:
        if candidate.name in _SETTINGS_BASENAMES and _HARNESS_DIR in candidate.parts:
            return True

    return False
