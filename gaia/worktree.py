"""
gaia.worktree -- creation and identity-locking of agentic git worktrees.

A worktree an agent creates for isolated repo work is born under Gaia's
central worktrees root (``gaia.paths.worktrees_dir()``), never inside the
repository it works on. That root exists precisely so this module never has
to ask what the target repo tracks: the native harness location
(``.claude/worktrees``, *inside* the repo) is safe only because Gaia's own
repo ignores that folder in block. At least one client repo tracks it in
git on purpose (a hundred committed files); a worktree born there would show
up as untracked changes someone could commit. Living under the central root
instead makes that impossible by construction -- the worktree is outside
every repository's working tree, so no repo's git status can see it at all.

The worktree's identity travels in its git lock's *reason*, never in its
directory name -- a deliberate, confirmed decision (do not revisit it): the
directory name is an opaque token (``secrets.token_hex``), and
``lock_reason`` / ``parse_lock_reason`` are the one place that encodes and
decodes "which contract and which agent created this" into the free-text
string ``git worktree lock --reason`` accepts. That reason is what task 14's
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

import re
import secrets
import subprocess
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
    carries no parseable identity, and a caller (task 14's collector) must
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
