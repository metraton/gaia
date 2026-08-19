"""Host-independent execution of writing specialist dispatches."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .worktree import WorktreeMetadata, _remove_created_worktree, create_canonical_worktree


@dataclass(frozen=True)
class WritingDispatchResult:
    """The identity returned to the caller after a writing dispatch completes."""

    path: str
    branch: str | None
    commit: str
    contract_id: str
    agent_id: str
    returncode: int
    stdout: str
    stderr: str

    def as_contract(self) -> dict[str, object]:
        """Return the literal AC-2 identity fields for a handoff or event."""
        return {
            "path": self.path,
            "branch": self.branch,
            "commit": self.commit,
            "contract_id": self.contract_id,
            "agent_id": self.agent_id,
        }


def _post_execution_metadata(metadata: WorktreeMetadata) -> WorktreeMetadata:
    """Read the identity that the specialist actually produced."""
    from .worktree import _git_value

    path = Path(metadata.path)
    return WorktreeMetadata(
        repo=metadata.repo,
        project=metadata.project,
        contract_id=metadata.contract_id,
        agent_id=metadata.agent_id,
        branch=_git_value(path, "branch", "--show-current"),
        commit=_git_value(path, "rev-parse", "HEAD"),
        lifecycle=metadata.lifecycle,
        path=metadata.path,
    )


def dispatch_writing_agent(
    repo_path: Path | str,
    workspace: Path | str,
    project: str,
    contract_id: str,
    agent_id: str,
    command: Sequence[str],
    *,
    branch: str | None = None,
    env: Mapping[str, str] | None = None,
) -> WritingDispatchResult:
    """Create, lock, and execute a writing specialist with the worktree as cwd.

    The command is a direct argv sequence, not a host-specific agent API. A
    failed specialist is rolled back so an unsuccessful dispatch cannot leave a
    locked, partially-owned worktree behind.
    """
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must be a non-empty argv sequence")
    dispatch_branch = branch or f"gaia/{agent_id}"
    metadata = create_canonical_worktree(
        repo_path, workspace, project, contract_id, agent_id, branch=dispatch_branch,
    )
    process_env = None if env is None else {**os.environ, **env}
    try:
        completed = subprocess.run(
            list(command), cwd=metadata.path, env=process_env,
            capture_output=True, text=True, check=False,
        )
        if completed.returncode != 0:
            _remove_created_worktree(Path(metadata.repo), Path(metadata.path))
            raise subprocess.CalledProcessError(
                completed.returncode, list(command), completed.stdout, completed.stderr,
            )
        final = _post_execution_metadata(metadata)
        result = WritingDispatchResult(
            path=final.path, branch=final.branch, commit=final.commit,
            contract_id=final.contract_id, agent_id=final.agent_id,
            returncode=completed.returncode,
            stdout=completed.stdout, stderr=completed.stderr,
        )
        return result
    except BaseException:
        if Path(metadata.path).exists():
            _remove_created_worktree(Path(metadata.repo), Path(metadata.path))
        raise
