"""
Workspace Type Detection

Detects whether the scan root is a single-repo, monorepo, or multi-repo workspace.
Called by the orchestrator before individual scanners run, and importable by scanners
that need workspace-aware behavior.

Detection logic:
  - If root has .git -> single-repo or monorepo (determined by stack scanner)
  - If root has NO .git but 2+ immediate subdirectories have .git -> multi-repo-workspace
  - Otherwise -> single-repo (default)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Directories to always skip during workspace scanning
_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".tox", ".venv",
    "venv", "dist", "build", ".next", ".nuxt", "target",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "vendor",
    ".terraform", ".terragrunt-cache",
})


@dataclass(frozen=True)
class WorkspaceInfo:
    """Result of workspace type detection.

    Attributes:
        workspace_type: One of 'single-repo', 'monorepo', 'multi-repo-workspace'.
        repo_dirs: For multi-repo, list of subdirectory Paths that contain .git.
                   Empty for single-repo/monorepo.
    """

    workspace_type: str = "single-repo"
    repo_dirs: List[Path] = field(default_factory=list)

    @property
    def is_multi_repo(self) -> bool:
        return self.workspace_type == "multi-repo-workspace"


def detect_workspace_type(root: Path) -> WorkspaceInfo:
    """Detect the workspace type for the given root directory.

    Args:
        root: Absolute path to the project root directory.

    Returns:
        WorkspaceInfo with the detected workspace type and repo directories.
    """
    # If root itself has .git, it's a normal repo (single or monorepo)
    if (root / ".git").is_dir():
        return WorkspaceInfo(workspace_type="single-repo")

    # Check immediate subdirectories for .git
    git_subdirs: List[Path] = []
    try:
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            if (entry / ".git").is_dir():
                git_subdirs.append(entry)
    except (PermissionError, OSError) as exc:
        logger.debug("Failed to scan subdirectories of %s: %s", root, exc)

    if len(git_subdirs) >= 2:
        logger.info(
            "Multi-repo workspace detected: %d repos in %s",
            len(git_subdirs),
            root,
        )
        return WorkspaceInfo(
            workspace_type="multi-repo-workspace",
            repo_dirs=git_subdirs,
        )

    return WorkspaceInfo(workspace_type="single-repo")
