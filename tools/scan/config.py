"""
Scanner Configuration Module

Provides ScanConfig for scanner orchestration settings, ToolCategory enum,
ToolDefinition dataclass, and the default tool definitions list.

Staleness threshold is overridable via GAIA_SCAN_STALENESS_HOURS env var.
"""

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional


class ToolCategory(Enum):
    """Tool categories for classification during environment scanning."""

    KUBERNETES = "kubernetes"
    CLOUD = "cloud"
    IAC = "iac"
    CONTAINER = "container"
    FILE_VIEWER = "file_viewer"
    FILE_SEARCH = "file_search"
    GIT = "git"
    LANGUAGE_RUNTIME = "language_runtime"
    BUILD = "build"
    UTILITY = "utility"
    AI_ASSISTANT = "ai_assistant"


@dataclass
class ToolDefinition:
    """Definition of a tool to scan for during environment detection.

    Attributes:
        name: Binary name (e.g., 'kubectl').
        category: Tool category enum value.
        version_flag: Flag to get version (default: '--version').
        version_regex: Regex to extract version from output (None = first line).
        preference_key: If this tool is a preferred alternative, the preference key.
        preference_priority: Higher = more preferred (e.g., bat=10, cat=1).
        extended: If True, only scanned with --full flag (low-value tools).
    """

    name: str
    category: ToolCategory
    version_flag: str = "--version"
    version_regex: Optional[str] = None
    preference_key: Optional[str] = None
    preference_priority: int = 0
    extended: bool = False


@dataclass
class ScanConfig:
    """Configuration for the scan orchestrator.

    Attributes:
        project_root: Absolute path to the project root directory.
        scanners: List of scanner names to run (empty = all).
        timeout_per_scanner: Timeout in seconds per scanner (default 10).
        parallel: Whether to run scanners in parallel (default True).
        verbose: Print detailed output (default False).
        output_path: Path to write project-context.json (None = default location).
        staleness_hours: Hours before a scan is considered stale (default 24).
    """

    project_root: Path = field(default_factory=lambda: Path.cwd())
    scanners: List[str] = field(default_factory=list)
    timeout_per_scanner: int = 10
    parallel: bool = True
    verbose: bool = False
    output_path: Optional[Path] = None
    staleness_hours: int = 24

    def __post_init__(self) -> None:
        """Apply environment variable overrides after init."""
        env_staleness = os.environ.get("GAIA_SCAN_STALENESS_HOURS")
        if env_staleness is not None:
            try:
                self.staleness_hours = int(env_staleness)
            except ValueError:
                pass  # Keep default if env var is not a valid integer


# Default tool definitions per data-model.md section 5.3
TOOL_DEFINITIONS: List[ToolDefinition] = [
    # Kubernetes
    ToolDefinition(
        name="kubectl",
        category=ToolCategory.KUBERNETES,
        version_flag="version --client",
        version_regex=r"Client Version:\s*(.+)",
    ),
    ToolDefinition(
        name="helm",
        category=ToolCategory.KUBERNETES,
        version_flag="version --short",
    ),
    ToolDefinition(name="kustomize", category=ToolCategory.KUBERNETES, extended=True),
    ToolDefinition(name="k9s", category=ToolCategory.KUBERNETES, extended=True),
    ToolDefinition(
        name="stern",
        category=ToolCategory.KUBERNETES,
        preference_key="log_viewer",
        preference_priority=10,
        extended=True,
    ),
    ToolDefinition(name="kubens", category=ToolCategory.KUBERNETES, extended=True),
    ToolDefinition(name="kubectx", category=ToolCategory.KUBERNETES, extended=True),
    # Cloud
    ToolDefinition(name="gcloud", category=ToolCategory.CLOUD),
    ToolDefinition(name="aws", category=ToolCategory.CLOUD),
    ToolDefinition(name="az", category=ToolCategory.CLOUD, extended=True),
    # IaC
    ToolDefinition(name="terraform", category=ToolCategory.IAC),
    ToolDefinition(name="terragrunt", category=ToolCategory.IAC),
    ToolDefinition(name="pulumi", category=ToolCategory.IAC, extended=True),
    # Container
    ToolDefinition(
        name="docker",
        category=ToolCategory.CONTAINER,
        preference_key="container_runtime",
        preference_priority=10,
    ),
    ToolDefinition(
        name="podman",
        category=ToolCategory.CONTAINER,
        preference_key="container_runtime",
        preference_priority=5,
        extended=True,
    ),
    ToolDefinition(
        name="nerdctl",
        category=ToolCategory.CONTAINER,
        preference_key="container_runtime",
        preference_priority=3,
        extended=True,
    ),
    # File viewing
    ToolDefinition(
        name="bat",
        category=ToolCategory.FILE_VIEWER,
        preference_key="file_viewer",
        preference_priority=10,
        extended=True,
    ),
    # File search
    ToolDefinition(
        name="fd",
        category=ToolCategory.FILE_SEARCH,
        preference_key="file_search",
        preference_priority=10,
        extended=True,
    ),
    ToolDefinition(
        name="rg",
        category=ToolCategory.FILE_SEARCH,
        preference_key="content_search",
        preference_priority=10,
        extended=True,
    ),
    ToolDefinition(name="fzf", category=ToolCategory.FILE_SEARCH, extended=True),
    # Git
    ToolDefinition(
        name="gh",
        category=ToolCategory.GIT,
        preference_key="git_cli",
        preference_priority=10,
    ),
    ToolDefinition(
        name="glab",
        category=ToolCategory.GIT,
        preference_key="git_cli",
        preference_priority=10,
    ),
    ToolDefinition(name="git", category=ToolCategory.GIT),
    # Language runtimes
    ToolDefinition(name="python3", category=ToolCategory.LANGUAGE_RUNTIME),
    ToolDefinition(name="node", category=ToolCategory.LANGUAGE_RUNTIME),
    ToolDefinition(name="go", category=ToolCategory.LANGUAGE_RUNTIME),
    ToolDefinition(name="cargo", category=ToolCategory.LANGUAGE_RUNTIME, extended=True),
    ToolDefinition(name="rustc", category=ToolCategory.LANGUAGE_RUNTIME, extended=True),
    ToolDefinition(name="java", category=ToolCategory.LANGUAGE_RUNTIME, extended=True),
    ToolDefinition(name="ruby", category=ToolCategory.LANGUAGE_RUNTIME, extended=True),
    ToolDefinition(name="php", category=ToolCategory.LANGUAGE_RUNTIME, extended=True),
    # Build tools
    ToolDefinition(name="make", category=ToolCategory.BUILD),
    ToolDefinition(name="npm", category=ToolCategory.BUILD),
    ToolDefinition(name="pnpm", category=ToolCategory.BUILD),
    ToolDefinition(name="yarn", category=ToolCategory.BUILD),
    ToolDefinition(name="pip", category=ToolCategory.BUILD),
    ToolDefinition(name="poetry", category=ToolCategory.BUILD, extended=True),
    ToolDefinition(name="gradle", category=ToolCategory.BUILD, extended=True),
    ToolDefinition(
        name="maven", category=ToolCategory.BUILD, version_flag="-version",
        extended=True,
    ),
    # Utilities
    ToolDefinition(name="jq", category=ToolCategory.UTILITY),
    ToolDefinition(name="yq", category=ToolCategory.UTILITY),
    ToolDefinition(name="curl", category=ToolCategory.UTILITY, extended=True),
    ToolDefinition(name="wget", category=ToolCategory.UTILITY, extended=True),
    # AI assistants
    ToolDefinition(name="claude", category=ToolCategory.AI_ASSISTANT),
]


def load_scan_config(project_root: Path) -> ScanConfig:
    """Load scan configuration from project-context.json if it exists.

    Reads metadata.scan_config from the project context file. Falls back
    to defaults if the file does not exist or the section is missing.

    Args:
        project_root: Absolute path to the project root directory.

    Returns:
        ScanConfig with values from file or defaults.
    """
    context_path = project_root / ".claude" / "project-context" / "project-context.json"

    config = ScanConfig(project_root=project_root)

    if context_path.is_file():
        try:
            with open(context_path, "r") as f:
                data = json.load(f)

            scan_config = data.get("metadata", {}).get("scan_config", {})

            if "staleness_hours" in scan_config:
                config.staleness_hours = int(scan_config["staleness_hours"])

        except (json.JSONDecodeError, ValueError, OSError):
            pass  # Use defaults on any error

    return config


DEFAULT_SCAN_CONFIG = ScanConfig()

# Path to the context-contracts.json file (relative to the gaia-ops-plugin root)
CONTRACT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "context-contracts.json"
