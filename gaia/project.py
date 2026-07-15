"""
gaia.project -- Workspace identity model and consolidate operations.

Workspace identity (``current()``) is PATH-based (M2-T7, AC-9): it is derived
from the repository's location on disk, not from the git remote, so it is
stable regardless of remote state and converges across vantage points (root,
subdirectory, linked worktree of the same repo all resolve identically).

Three-level resolution:
  1. Git repo -> basename of the repository ROOT (via git-common-dir)
  2. Directory name in lowercase (when not a git repo)
  3. Literal ``"global"`` (when neither a git repo nor an identifiable name)

The remote-derived canonical identity (``host/owner/repo``) is still produced
by :func:`_normalize_remote`, but it is captured separately in the
``workspaces.identity`` column by the store writer, which reads the remote
directly -- it no longer flows through ``current()``.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram.

Public API::

    from gaia.project import current, merge, list_known, MergeResult
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Identity: current()
# ---------------------------------------------------------------------------

def _normalize_remote(url: str) -> str:
    """Normalize a git remote URL to the canonical ``host/owner/repo`` form.

    Examples:
        ``git@github.com:metraton/Gaia.git``       -> ``github.com/metraton/gaia``
        ``https://github.com/Metraton/Gaia.git``   -> ``github.com/metraton/gaia``
        ``https://bitbucket.org/aaxisdigital/bildwiz.git``
                                                   -> ``bitbucket.org/aaxisdigital/bildwiz``

    Returns:
        Canonical lowercase ``host/owner/repo`` string, or empty string if the
        input cannot be normalized.
    """
    s = url.strip().lower()
    if not s:
        return ""

    # Strip protocol prefixes
    for prefix in ("https://", "http://", "ssh://", "git+ssh://", "git+https://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    # SSH form: git@host:owner/repo -> host/owner/repo
    if s.startswith("git@"):
        s = s[len("git@"):]
        # Convert the first ':' (host:path separator) to '/'
        if ":" in s:
            host, _, rest = s.partition(":")
            s = f"{host}/{rest}"

    # Strip trailing .git
    if s.endswith(".git"):
        s = s[: -len(".git")]

    # Strip trailing slashes
    s = s.rstrip("/")

    return s


def _git_remote_origin(cwd: Path) -> str | None:
    """Return the git remote `origin` URL or None if unavailable.

    Uses subprocess with a short timeout. Never raises -- returns None on
    any failure (no git, not a repo, no origin remote, timeout).
    """
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    url = (result.stdout or "").strip()
    return url or None


def git_common_dir(cwd: Path | str | None = None) -> str | None:
    """Return the realpath of the git common directory for ``cwd``, or None.

    ``git rev-parse --git-common-dir`` resolves to the SHARED ``.git`` directory
    of a repository -- the same path whether invoked from the repo root, a
    nested subdirectory, or a linked worktree. This makes it a stable,
    vantage-independent fingerprint of a physical repository: the same repo
    scanned from two different roots yields the same common dir.

    Unlike ``--git-dir`` (which differs per worktree), ``--git-common-dir`` is
    identical across the main checkout and all its worktrees, so it collapses
    worktrees of the same repo to one identity.

    The returned path is the absolute, symlink-resolved (``realpath``) form so
    that two paths reaching the same directory via different symlinks compare
    equal.

    Uses subprocess with a short timeout. Never raises -- returns None on any
    failure (no git, not a repo, timeout).
    """
    if shutil.which("git") is None:
        return None
    target = Path(cwd) if cwd is not None else Path.cwd()
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    # git may return a path relative to `target` (e.g. ".git"); resolve it
    # against the target directory, then realpath to canonicalize symlinks.
    p = Path(raw)
    if not p.is_absolute():
        p = target / p
    try:
        return str(p.resolve())
    except (OSError, RuntimeError):
        return None


def current(cwd: Path | str | None = None) -> str:
    """Return the workspace identity for the given directory -- PATH-BASED.

    Resolution is PATH-first (M2-T7, AC-9), NOT git-remote-first. The identity
    is derived from the repository's own location on disk, so it is stable
    regardless of remote state and converges across vantage points:

      1. Git repo -> the basename of the REPOSITORY ROOT, resolved via
         ``git rev-parse --git-common-dir`` (:func:`git_common_dir`). Because
         the common dir is identical from the repo root, any nested
         subdirectory, and any linked worktree, two different working-directory
         paths of the SAME repo always resolve to the SAME identity -- and the
         git remote never decides the identity ahead of the path.
      2. Not a git repo -> the basename of ``cwd`` in lowercase.
      3. Literal ``"global"`` (no git, no identifiable directory name).

    The git remote URL is deliberately NOT consulted here. The remote-derived
    canonical identity still exists, but it is captured separately in the
    ``workspaces.identity`` column by the store writer
    (``gaia/store/writer.py::_resolve_identity``), which reads the remote
    directly -- so ``current()`` answering "which workspace am I in" stays
    coherent with the path-anchored scan model (workspace -> proyecto -> repo)
    while the remote identity is preserved where it belongs.

    Args:
        cwd: Directory to resolve identity for. Defaults to ``Path.cwd()``.

    Returns:
        Workspace identity string. Never empty, never raises.
    """
    target = Path(cwd) if cwd is not None else Path.cwd()
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        # If resolve fails (broken symlink, permission), fall back to global
        return "global"

    # Level 1 (PATH-based): repository root basename, vantage-independent.
    # git_common_dir collapses root / subdir / worktree of the same repo to
    # one path, so the identity does not diverge across vantage points and
    # does not depend on the remote.
    common = git_common_dir(target)
    if common:
        # The common dir (e.g. ``/x/repo/.git``) sits inside the repo root;
        # its parent is the repository root directory.
        repo_root = Path(common).parent
        name = repo_root.name.lower().strip()
        if name:
            return name

    # Level 2: directory basename (not a git repo).
    name = target.name.lower().strip()
    if name:
        return name

    # Level 3: global
    return "global"


def resolve_workspace(cwd: Path | str | None = None) -> str:
    """Resolve the workspace identity to attribute a write to -- NEVER empty.

    This is the canonical env-aware cascade wrapped around :func:`current`.
    It is the single source of truth for "which workspace should this write
    land under" and is shared by every writer that must attribute a row --
    the harness event writer, episodic memory, etc. -- so the resolution
    order does not drift between call sites.

    Resolution order:
        1. ``GAIA_DISPATCH_WORKSPACE`` environment variable (set by the
           SubagentStop hook chain when dispatching a subagent).
        2. ``GAIA_WORKSPACE`` environment variable (set by
           ``gaia <cmd> --workspace=<name>``).
        3. :func:`current` -- path-based workspace identity derived from the
           repository/directory of *cwd*.
        4. Literal ``"global"`` when nothing else resolves (or *current*
           raises / returns empty).

    The explicit env vars win over the path-derived identity because a
    dispatch may run from a cwd that is not the target workspace; the env var
    carries the intended attribution. Falling back to ``"global"`` (never
    ``None``) keeps attribution consistent with episodic memory and the
    handoff persister, which use the same final fallback.

    Args:
        cwd: Directory to resolve the path-based identity for (step 3).
            Defaults to :func:`current`'s own default (``Path.cwd()``).

    Returns:
        Workspace identity string. Never empty, never ``None``, never raises.
    """
    import os as _os

    for env_key in ("GAIA_DISPATCH_WORKSPACE", "GAIA_WORKSPACE"):
        value = _os.environ.get(env_key)
        if value:
            return value
    try:
        ws = current(cwd)
        if ws:
            return ws
    except Exception:
        pass
    return "global"


# ---------------------------------------------------------------------------
# Consolidate: merge()
# ---------------------------------------------------------------------------

@dataclass
class MergeResult:
    """Result of a workspace merge operation.

    Attributes:
        preview: list of (relative_path, size_bytes) tuples that would move (or moved)
        conflicts: list of relative paths that exist in both source and target
        moved: list of relative paths actually moved (only populated when confirm=True)
    """
    preview: list[tuple[str, int]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)


def _walk_files(root: Path):
    """Yield (relative_path_str, size_bytes) for every file under root."""
    if not root.is_dir():
        return
    for p in root.rglob("*"):
        if p.is_file():
            yield (str(p.relative_to(root)), p.stat().st_size)


def merge(
    from_id: str,
    to_id: str,
    *,
    confirm: bool = False,
) -> MergeResult:
    """Merge files from one workspace directory into another.

    Operates on directories under ``workspaces_dir() / <id>``. Without
    ``confirm=True``, only previews the operation. With ``confirm=True``,
    moves non-conflicting files; conflicts (same relative path on both sides)
    are reported and NOT overwritten.

    Idempotent: if ``from_id`` does not exist, returns an empty result without
    error. If ``from_id == to_id``, returns an empty result (no-op).

    Args:
        from_id: Source workspace identity (e.g. ``"github.com/owner/old-repo"``).
        to_id: Target workspace identity.
        confirm: If True, actually move files. If False (default), preview only.

    Returns:
        MergeResult with preview, conflicts, and moved lists populated.
    """
    from gaia.paths import workspaces_dir

    result = MergeResult()

    # No-op cases
    if from_id == to_id:
        return result

    src = workspaces_dir() / from_id
    dst = workspaces_dir() / to_id

    if not src.is_dir():
        # Idempotent: source already merged or never existed
        return result

    # Build preview and conflict lists
    for rel, size in _walk_files(src):
        target_path = dst / rel
        if target_path.exists():
            result.conflicts.append(rel)
        else:
            result.preview.append((rel, size))

    if not confirm:
        return result

    # Execute moves for non-conflicting files
    for rel, _size in result.preview:
        src_file = src / rel
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.rename(dst_file)
        result.moved.append(rel)

    # If everything moved cleanly and src is empty, clean up empty dirs
    if not result.conflicts:
        for d in sorted((p for p in src.rglob("*") if p.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            src.rmdir()
        except OSError:
            pass

    return result


# ---------------------------------------------------------------------------
# Discovery: list_known()
# ---------------------------------------------------------------------------

def list_known() -> list[str]:
    """Return the list of known workspace identities (directories under workspaces_dir).

    Returns:
        Sorted list of workspace identity strings. Empty if workspaces_dir
        does not exist.
    """
    from gaia.paths import workspaces_dir

    base = workspaces_dir()
    if not base.is_dir():
        return []
    # Workspaces are nested: host/owner/repo. Walk three levels deep when present,
    # but also surface flat names (fallback identities like "my-project").
    result: set[str] = set()
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        # Try to detect canonical host/owner/repo nesting
        for owner in entry.iterdir() if entry.is_dir() else []:
            if not owner.is_dir():
                continue
            for repo in owner.iterdir() if owner.is_dir() else []:
                if repo.is_dir():
                    result.add(f"{entry.name}/{owner.name}/{repo.name}")
        # Also include flat names (directory-name fallback identities)
        # Only add if no nested host/owner/repo was found under this entry
        if not any((entry / o).is_dir() and any((entry / o / r).is_dir() for r in (entry / o).iterdir() if (entry / o).is_dir()) for o in entry.iterdir() if entry.is_dir()):
            result.add(entry.name)
    return sorted(result)
