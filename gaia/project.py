"""
gaia.project -- Workspace identity model and consolidate operations.

Workspace identity is derived from the git remote URL, normalized to the
canonical form ``host/owner/repo`` (lowercase, no protocol, no .git suffix).

Three-level fallback:
  1. Git remote URL normalized to canonical form (primary)
  2. Directory name in lowercase (when no git remote)
  3. Literal ``"global"`` (when neither git remote nor identifiable directory)

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


def current(cwd: Path | str | None = None) -> str:
    """Return the workspace identity for the given directory.

    Three-level fallback:
      1. Git remote URL normalized to canonical form (``host/owner/repo``)
      2. Directory basename in lowercase
      3. Literal ``"global"`` (no git, no identifiable directory)

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

    # Level 1: git remote URL
    remote = _git_remote_origin(target)
    if remote:
        normalized = _normalize_remote(remote)
        if normalized:
            return normalized

    # Level 2: directory basename
    name = target.name.lower().strip()
    if name:
        return name

    # Level 3: global
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
