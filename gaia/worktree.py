"""
gaia.worktree -- creation, identity and managed roots of agentic git worktrees.

A worktree is born at ``<workspace>/.project-worktrees/<project>/<id>``: the
workspace is the directory of the Gaia workspace that owns the repository, so
the user can open the isolated work and its diff from their own explorer. The
root carries a ``.gitignore`` of ``*`` because a workspace may itself be a git
repository, or be the very repository the worktree branches from, and a
worktree must never surface there as untracked changes -- at least one client
repo versions the harness-native ``.claude/worktrees`` folder, which is why no
in-repo location is trusted without that ignore file.

Worktrees created earlier under the central ``gaia.paths.worktrees_dir()``
stay where they are and stay managed: ``managed_root_containing`` recognises
both roots, and enumeration goes through each repository's own git registry,
which is indifferent to where a worktree lives.

The identity sidecar is written into the worktree's private git directory,
outside its working tree, so no ``git add`` of any form can version it.
Worktrees born before that carry it in-tree, where it is still read.

The contract and agent identity also travel in the git lock's *reason*, never
in the directory name, which is an opaque token -- a confirmed decision, do not
revisit it. ``lock_reason`` / ``parse_lock_reason`` encode and decode it, and
the retention collector reads it to judge whether an abandoned, still-locked
worktree's owning session is alive.

Verified on git 2.43.0: the lock reason round-trips through ``git worktree list
--porcelain``, survives the working directory being deleted, and a locked
entry is never prunable -- unforced ``remove`` and ``prune`` both refuse it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from gaia.paths import worktrees_dir
from gaia.project import containing_workspace, git_common_dir

_REASON_TAG = "gaia-agentic-worktree"
_REASON_RE = re.compile(
    r"^"
    + re.escape(_REASON_TAG)
    + r" contract_id=(?P<contract_id>\S+) agent_id=(?P<agent_id>\S+)$"
)

_TOKEN_HEX_BYTES = 8
_METADATA_FILENAME = "gaia-worktree.json"
_LEGACY_METADATA_FILENAME = ".gaia-worktree.json"
_WORKSPACE_ROOT_DIRNAME = ".project-worktrees"
_LIFECYCLE_CREATED = "created"
_PROJECT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorktreePathError(ValueError):
    """Raised when a worktree's location, base or identity cannot be resolved safely."""


@dataclass(frozen=True)
class WorktreeMetadata:
    """Auditable identity persisted beside a Gaia-managed worktree."""

    repo: str
    project: str
    contract_id: str
    agent_id: str
    branch: Optional[str]
    commit: str
    lifecycle: str
    path: str

    def as_dict(self) -> dict[str, object]:
        """Return the stable, JSON-safe metadata representation."""
        return {
            "repo": self.repo,
            "project": self.project,
            "contract_id": self.contract_id,
            "agent_id": self.agent_id,
            "branch": self.branch,
            "commit": self.commit,
            "lifecycle": self.lifecycle,
            "path": self.path,
        }


def _git_value(repo_path: Path, *args: str) -> str:
    """Read one required identity value from git, failing closed on ambiguity."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args], capture_output=True, text=True, check=True
    )
    value = result.stdout.strip()
    if not value:
        raise WorktreePathError(f"git returned no value for {' '.join(args)}")
    return value


def worktree_metadata_path(worktree_path: Path | str) -> Path:
    """Return the sidecar path inside the worktree's private git directory."""
    git_dir = _git_value(Path(worktree_path), "rev-parse", "--absolute-git-dir")
    return Path(git_dir) / _METADATA_FILENAME


def legacy_metadata_path(worktree_path: Path | str) -> Path:
    """Return the in-tree sidecar path used by worktrees created before the git-dir sidecar."""
    return Path(worktree_path) / _LEGACY_METADATA_FILENAME


def _main_checkout(path: Path) -> Path:
    """Return the main working tree of the repository *path* belongs to."""
    common = git_common_dir(path)
    if common is None:
        raise WorktreePathError(f"{path} is not inside a git repository")
    return Path(common).parent


def workspace_worktrees_root(repo_path: Path | str) -> Path:
    """Return ``<workspace>/.project-worktrees`` for the Gaia workspace owning *repo_path*.

    Resolved from the repository's main checkout, so a linked worktree of the
    same repository answers the same root. Raises ``WorktreePathError`` when
    the workspace name matches no directory at or above that checkout.
    """
    checkout = _main_checkout(Path(repo_path).resolve())
    workspace = containing_workspace(checkout).lower()
    for directory in (checkout, *checkout.parents):
        if directory.name.lower() == workspace:
            return directory / _WORKSPACE_ROOT_DIRNAME
    raise WorktreePathError(
        f"workspace {workspace!r} names no directory at or above {checkout}"
    )


def managed_root_containing(path: Path | str) -> Optional[Path]:
    """Return the managed worktrees root strictly containing *path*, or None.

    The candidates are the workspace root of the repository *path* belongs to
    and the legacy central root, both compared by realpath.
    """
    real = Path(os.path.realpath(path))
    roots = []
    try:
        roots.append(Path(os.path.realpath(workspace_worktrees_root(real))))
    except WorktreePathError:
        pass
    roots.append(Path(os.path.realpath(worktrees_dir())))
    for root in roots:
        if root in real.parents:
            return root
    return None


def _default_remote(repo: Path) -> str:
    """Return ``origin`` when present, else the only remote, else fail closed."""
    remotes = subprocess.run(
        ["git", "-C", str(repo), "remote"], capture_output=True, text=True, check=True
    ).stdout.split()
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    raise WorktreePathError("cannot choose a remote to branch from; pass an explicit base")


def resolve_default_base(repo_path: Path | str) -> str:
    """Fetch the remote's default branch and return the commit it now points to."""
    repo = Path(repo_path)
    remote = _default_remote(repo)
    advertised = _git_value(repo, "ls-remote", "--symref", remote, "HEAD")
    default = None
    for line in advertised.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            default = line[len("ref: refs/heads/"):-len("\tHEAD")]
    if not default:
        raise WorktreePathError(
            f"remote {remote!r} advertises no default branch; pass an explicit base"
        )
    tracking = f"refs/remotes/{remote}/{default}"
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "--quiet", remote, f"+refs/heads/{default}:{tracking}"],
        check=True, capture_output=True, text=True,
    )
    return _git_value(repo, "rev-parse", "--verify", f"{tracking}^{{commit}}")


def _resolve_base(repo: Path, base: Optional[str]) -> str:
    """Return the commit a new worktree starts from; never the checkout's HEAD by default."""
    if base is None:
        return resolve_default_base(repo)
    return _git_value(repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")


def _ensure_root_ignored(root: Path) -> None:
    """Create *root* with a ``.gitignore`` that hides it, and itself, from any enclosing repo."""
    root.mkdir(parents=True, exist_ok=True)
    ignore_file = root / ".gitignore"
    if not ignore_file.exists():
        ignore_file.write_text("*\n", encoding="utf-8")


def create_canonical_worktree(
    repo_path: Path | str,
    project: str,
    contract_id: str,
    agent_id: str,
    *,
    branch: Optional[str] = None,
    base: Optional[str] = None,
) -> WorktreeMetadata:
    """Create and lock a worktree, with metadata, under the workspace's project root.

    The branch starts at *base* when given, otherwise at the freshly fetched
    default branch of the repository's remote. Without *branch*, the branch is
    named after the worktree id.
    """
    repo = Path(repo_path).resolve()
    if not _PROJECT_SEGMENT_RE.match(project):
        raise WorktreePathError(f"project {project!r} is not a single path segment")
    root = workspace_worktrees_root(repo)
    base_commit = _resolve_base(repo, base)
    _ensure_root_ignored(root)
    project_root = root / project
    project_root.mkdir(exist_ok=True)
    worktree_id = secrets.token_hex(16)
    target = project_root / worktree_id
    if target.exists() or target.is_symlink():
        raise WorktreePathError("generated worktree id collided")

    add_cmd = [
        "git", "-C", str(repo), "worktree", "add", "--quiet",
        "-b", branch or worktree_id, str(target), base_commit,
    ]
    try:
        subprocess.run(add_cmd, check=True, capture_output=True, text=True)
    except Exception:
        _remove_created_worktree(repo, target)
        raise
    try:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "lock", str(target), "--reason", lock_reason(contract_id, agent_id)],
            check=True, capture_output=True, text=True,
        )
        resolved_target = target.resolve()
        if resolved_target.parent != project_root.resolve():
            raise WorktreePathError("git worktree escaped its project root")
        metadata = WorktreeMetadata(
            repo=str(repo),
            project=project,
            contract_id=contract_id,
            agent_id=agent_id,
            branch=_git_value(resolved_target, "branch", "--show-current"),
            commit=_git_value(resolved_target, "rev-parse", "HEAD"),
            lifecycle=_LIFECYCLE_CREATED,
            path=str(resolved_target),
        )
        metadata_path = worktree_metadata_path(resolved_target)
        metadata_path.write_text(json.dumps(metadata.as_dict(), sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(metadata_path, 0o600)
        return metadata
    except Exception:
        _remove_created_worktree(repo, target)
        raise


def _remove_created_worktree(repo: Path, target: Path) -> None:
    """Best-effort rollback for a worktree that never reached dispatch."""
    try:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "unlock", str(target)],
            check=False, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(target)],
            check=False, capture_output=True, text=True,
        )
    except OSError:
        pass


_METADATA_REQUIRED_KEYS = {
    "repo", "project", "contract_id", "agent_id", "branch", "commit", "lifecycle", "path",
}


def _parse_sidecar(metadata_path: Path, path: Path) -> Optional[WorktreeMetadata]:
    """Parse *metadata_path* as *path*'s own identity, or return None.

    Valid only with exactly the required key set and a ``path`` that resolves
    to *path* itself: a same-named file is never trusted by its name alone.
    """
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or set(raw) != _METADATA_REQUIRED_KEYS:
        return None
    try:
        if Path(str(raw["path"])).resolve() != path:
            return None
    except (TypeError, ValueError, OSError):
        return None
    return WorktreeMetadata(**raw)


def _existing_sidecar(path: Path) -> Optional[Path]:
    """Return the git-dir sidecar when present, else the legacy in-tree one, else None."""
    try:
        own = worktree_metadata_path(path)
    except (OSError, subprocess.CalledProcessError, WorktreePathError):
        own = None
    for candidate in (own, legacy_metadata_path(path)):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def is_valid_own_metadata_sidecar(worktree_path: Path | str) -> bool:
    """True when the legacy in-tree sidecar parses as *this* worktree's own identity.

    Retention exempts that file from the dirtiness predicate by content, never
    by name, so an agent cannot hide work in a same-named file.
    """
    path = Path(worktree_path).resolve()
    return _parse_sidecar(legacy_metadata_path(path), path) is not None


def read_worktree_metadata(worktree_path: Path | str) -> Optional[WorktreeMetadata]:
    """Read canonical metadata, or a complete legacy lock identity without migrating it."""
    path = Path(worktree_path).resolve()
    sidecar = _existing_sidecar(path)
    if sidecar is not None:
        return _parse_sidecar(sidecar, path)
    try:
        repo = Path(_git_value(path, "rev-parse", "--git-common-dir")).resolve().parent
        listing = _git_value(repo, "worktree", "list", "--porcelain")
    except (OSError, subprocess.CalledProcessError, WorktreePathError):
        return None
    reason = None
    current_path = str(path)
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif current_path == str(path) and line.startswith("locked "):
            reason = line[len("locked "):]
            break
    identity = parse_lock_reason(reason)
    if identity is None:
        return None
    return WorktreeMetadata(
        repo=str(repo), project=path.parent.name, contract_id=identity["contract_id"],
        agent_id=identity["agent_id"], branch=None, commit=_git_value(path, "rev-parse", "HEAD"),
        lifecycle="legacy", path=str(path),
    )


def lock_reason(contract_id: str, agent_id: str) -> str:
    """Build the free-text reason ``git worktree lock --reason`` carries.

    ``parse_lock_reason`` is its exact inverse; both fields are required.
    """
    return f"{_REASON_TAG} contract_id={contract_id} agent_id={agent_id}"


def parse_lock_reason(reason: Optional[str]) -> Optional[Dict[str, str]]:
    """Recover ``{"contract_id": ..., "agent_id": ...}`` from a lock reason.

    Returns ``None`` when *reason* was not minted by ``lock_reason``: a
    worktree locked by a human or another tool has unknown ownership.
    """
    if not reason:
        return None
    match = _REASON_RE.match(reason.strip())
    if not match:
        return None
    return {
        "contract_id": match.group("contract_id"),
        "agent_id": match.group("agent_id"),
    }


def create_agentic_worktree(
    repo_path: Path,
    contract_id: str,
    agent_id: str,
    *,
    branch: Optional[str] = None,
) -> Path:
    """Create a locked worktree for *repo_path* under the legacy central root, without metadata.

    Returns the realpath of the created, locked worktree. Raises
    ``subprocess.CalledProcessError`` on any git failure; callers decide
    whether a failed lock should also unwind the ``add``.
    """
    root = worktrees_dir()
    root.mkdir(parents=True, exist_ok=True)
    target = root / secrets.token_hex(_TOKEN_HEX_BYTES)

    add_cmd = ["git", "-C", str(repo_path), "worktree", "add", "--quiet", str(target)]
    if branch:
        add_cmd += ["-b", branch]
    subprocess.run(add_cmd, check=True, capture_output=True, text=True)

    reason = lock_reason(contract_id, agent_id)
    subprocess.run(
        [
            "git", "-C", str(repo_path),
            "worktree", "lock", str(target),
            "--reason", reason,
        ],
        check=True, capture_output=True, text=True,
    )
    return Path(str(target)).resolve()
