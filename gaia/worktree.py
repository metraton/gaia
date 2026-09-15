"""
gaia.worktree -- creation and identity-locking of agentic git worktrees.

A worktree an agent creates for isolated repo work is born under Gaia's one
central root, ``gaia.paths.worktrees_dir()`` (``~/.gaia/worktrees`` by
default, relocated whole by ``GAIA_DATA_DIR``), never inside the repository
it works on. That root exists precisely so this module never has to ask what
the target repo tracks: the native harness location (``.claude/worktrees``,
*inside* the repo) is safe only because Gaia's own repo ignores that folder
in block. At least one client repo tracks it in git on purpose (a hundred
committed files); a worktree born there would show up as untracked changes
someone could commit. Living under the central root instead makes that
impossible by construction -- the worktree is outside every repository's
working tree, so no repo's git status can see it at all. It is also the
exact root ``hooks/modules/security/mutative_verbs.py::_gaia_worktrees_root``
resolves for the T0 recycling exemption on ``git worktree remove`` -- a
single root means every worktree this module creates is, by construction,
inside the scope that exemption already covers.

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
    project: str,
    contract_id: str,
    agent_id: str,
    *,
    branch: Optional[str] = None,
) -> WorktreeMetadata:
    """Create and lock a worktree, with metadata, under Gaia's central worktrees root."""
    repo = Path(repo_path).resolve()
    root = worktrees_dir()
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
