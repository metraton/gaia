"""
gaia.worktree -- creation and identity-locking of agentic git worktrees.

A canonical worktree an agent creates for isolated repo work is born under the
workspace-owned ``<workspace>/.project-worktrees/<project>/`` root, never inside the
repository it works on. That root exists precisely so this module never has
to ask what the target repo tracks: the native harness location
(``.claude/worktrees``, *inside* the repo) is safe only because Gaia's own
repo ignores that folder in block. At least one client repo tracks it in
git on purpose (a hundred committed files); a worktree born there would show
up as untracked changes someone could commit. Living under the managed root
instead makes that impossible by construction -- the worktree is outside
every repository's working tree, so no repo's git status can see it at all.

Canonical metadata is the primary identity record. The worktree's contract
and agent identity also travel in its git lock's *reason*, never in its
directory name -- a deliberate, confirmed decision (do not revisit it): the
directory name is an opaque token (``secrets.token_hex``), and
``lock_reason`` / ``parse_lock_reason`` are the compatibility path that encodes and
decodes "which contract and which agent created this" into the free-text
string ``git worktree lock --reason`` accepts. That reason is what task 15's
worktree collector reads -- cross-referenced against
``gaia.retention.liveness`` -- to decide whether an abandoned, still-locked
worktree's owning session is still alive.

Empirically verified on git 2.43.0: the lock reason round-trips intact
through ``git worktree list --porcelain``, the lock survives the working
directory being deleted out from under it, and a locked entry never appears
as prunable while the lock stands -- ``git worktree remove`` (unforced) and
``git worktree prune`` both refuse it.
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

_REASON_TAG = "gaia-agentic-worktree"
_REASON_RE = re.compile(
    r"^"
    + re.escape(_REASON_TAG)
    + r" contract_id=(?P<contract_id>\S+) agent_id=(?P<agent_id>\S+)$"
)

_TOKEN_HEX_BYTES = 8
_METADATA_FILENAME = ".gaia-worktree.json"
_LIFECYCLE_CREATED = "created"


class WorktreePathError(ValueError):
    """Raised when a worktree identity would leave its managed root."""


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


def _safe_component(value: str, label: str) -> str:
    """Accept one non-empty path component and reject traversal syntax."""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise WorktreePathError(f"invalid {label} path component")
    if "\x00" in value or "/" in value or "\\" in value or Path(value).is_absolute():
        raise WorktreePathError(f"invalid {label} path component")
    return value


def workspace_worktrees_root(workspace: Path | str, project: str) -> Path:
    """Resolve the workspace-owned root for one project without following escapes."""
    workspace_path = Path(workspace).resolve()
    if not workspace_path.is_dir():
        raise WorktreePathError("workspace must be an existing directory")
    project_name = _safe_component(project, "project")
    root = workspace_path / ".project-worktrees" / project_name
    resolved_root = root.resolve()
    if resolved_root != workspace_path / ".project-worktrees" / project_name:
        raise WorktreePathError("project root resolves through a symlink")
    if workspace_path not in resolved_root.parents:
        raise WorktreePathError("project root escapes workspace")
    return resolved_root


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
    """Return the metadata sidecar path for a managed worktree."""
    return Path(worktree_path) / _METADATA_FILENAME


def create_canonical_worktree(
    repo_path: Path | str,
    workspace: Path | str,
    project: str,
    contract_id: str,
    agent_id: str,
    *,
    branch: Optional[str] = None,
) -> WorktreeMetadata:
    """Create and lock a worktree under the workspace-owned canonical root."""
    repo = Path(repo_path).resolve()
    workspace_path = Path(workspace).resolve()
    root = workspace_worktrees_root(workspace_path, project)
    if root == repo or repo in root.parents:
        raise WorktreePathError("canonical worktree root must be outside the checkout")
    root.mkdir(parents=True, exist_ok=True)
    worktree_id = secrets.token_hex(16)
    target = root / worktree_id
    if target.exists() or target.is_symlink():
        raise WorktreePathError("generated worktree id collided")

    add_cmd = ["git", "-C", str(repo), "worktree", "add", "--quiet", str(target)]
    if branch:
        add_cmd += ["-b", branch]
    subprocess.run(add_cmd, check=True, capture_output=True, text=True)
    try:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "lock", str(target), "--reason", lock_reason(contract_id, agent_id)],
            check=True, capture_output=True, text=True,
        )
        resolved_target = target.resolve()
        if root not in resolved_target.parents:
            raise WorktreePathError("git worktree escaped canonical root")
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
        # Do not force-remove a possibly dirty worktree; callers must resolve it explicitly.
        raise


def read_worktree_metadata(worktree_path: Path | str) -> Optional[WorktreeMetadata]:
    """Read canonical metadata, or a complete legacy lock identity without migrating it."""
    path = Path(worktree_path).resolve()
    metadata_path = worktree_metadata_path(path)
    if metadata_path.is_file():
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        required = {"repo", "project", "contract_id", "agent_id", "branch", "commit", "lifecycle", "path"}
        if set(raw) != required or Path(raw["path"]).resolve() != path:
            return None
        return WorktreeMetadata(**raw)
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


create_workspace_worktree = create_canonical_worktree


def lock_reason(contract_id: str, agent_id: str) -> str:
    """Build the free-text reason ``git worktree lock --reason`` carries.

    This is the ONLY place the reason's shape is defined; ``parse_lock_reason``
    is its exact inverse. Both fields are required -- a worktree's identity is
    incomplete without knowing both which contract created it and which agent
    ran that contract.
    """
    return f"{_REASON_TAG} contract_id={contract_id} agent_id={agent_id}"


def parse_lock_reason(reason: Optional[str]) -> Optional[Dict[str, str]]:
    """Recover ``{"contract_id": ..., "agent_id": ...}`` from a lock reason.

    Returns ``None`` when *reason* was not minted by ``lock_reason`` -- a
    worktree locked by a human or another tool for an unrelated purpose
    carries no parseable identity, and a caller (task 15's collector) must
    treat that as unknown rather than guess at ownership.
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
    """Create a locked worktree for *repo_path* under Gaia's central root.

    Runs ``git worktree add`` targeting a fresh, opaquely-named directory
    under ``worktrees_dir()`` (never inside *repo_path*), then immediately
    ``git worktree lock``s it with ``lock_reason(contract_id, agent_id)`` so
    the identity survives even after the working directory is later removed.

    Returns the realpath of the created, locked worktree. Raises
    ``subprocess.CalledProcessError`` on any git failure -- callers decide
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
