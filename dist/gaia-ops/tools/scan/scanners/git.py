"""
Git Scanner

Detects git platform, remotes, default branch, branch strategy, and monorepo
workspace configuration from the project's .git directory and manifest files.

Returns the `git` section per data-model.md section 2.5.

Pure Function Contract:
- No file writes
- No state modification
- No network calls
- Only reads: filesystem reads (`.git/config`, `.git/HEAD`, manifest files)
"""

import configparser
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.scan.scanners.base import BaseScanner, ScanResult

logger = logging.getLogger(__name__)

# Known platform domain patterns
_PLATFORM_PATTERNS: List[Tuple[str, str]] = [
    ("github.com", "github"),
    ("gitlab.com", "gitlab"),
    ("bitbucket.org", "bitbucket"),
]

# Monorepo indicator files and their workspace config names
_MONOREPO_INDICATORS: List[Tuple[str, str]] = [
    ("turbo.json", "turborepo"),
    ("nx.json", "nx"),
    ("lerna.json", "lerna"),
    ("pnpm-workspace.yaml", "pnpm"),
]


def _detect_platform_from_url(url: str) -> Optional[str]:
    """Detect git hosting platform from a remote URL.

    Supports SSH (git@host:...) and HTTPS (https://host/...) URLs.

    Args:
        url: Git remote URL string.

    Returns:
        Platform name ('github', 'gitlab', 'bitbucket', 'self-hosted') or None.
    """
    if not url:
        return None

    url_lower = url.lower()

    for domain, platform in _PLATFORM_PATTERNS:
        if domain in url_lower:
            return platform

    # If there is a host but it doesn't match known platforms, it's self-hosted.
    # Check for patterns like git@host: or https://host/
    if re.search(r"(://|@)", url):
        return "self-hosted"

    return None


def _parse_git_config(git_dir: Path) -> Dict[str, Any]:
    """Parse .git/config to extract remotes.

    Uses configparser which handles the INI-like git config format.
    Falls back gracefully on parse errors.

    Args:
        git_dir: Path to the .git directory.

    Returns:
        Dict with 'remotes' list of {name, url, platform}.
    """
    config_path = git_dir / "config"
    remotes: List[Dict[str, Any]] = []

    if not config_path.is_file():
        return {"remotes": remotes}

    try:
        parser = configparser.ConfigParser(strict=False)
        parser.read(str(config_path))

        for section in parser.sections():
            # Git config remote sections look like: [remote "origin"]
            if section.startswith('remote "') and section.endswith('"'):
                remote_name = section[8:-1]  # Strip 'remote "' and '"'
                url = parser.get(section, "url", fallback="")
                platform = _detect_platform_from_url(url)
                remotes.append({
                    "name": remote_name,
                    "url": url,
                    "platform": platform,
                })

    except (configparser.Error, OSError) as exc:
        logger.debug("Failed to parse git config at %s: %s", config_path, exc)

    return {"remotes": remotes}


def _detect_default_branch(git_dir: Path) -> Optional[str]:
    """Detect the default branch from .git/HEAD.

    The HEAD file contains either:
    - 'ref: refs/heads/<branch>' for a branch reference
    - A commit hash for detached HEAD

    Args:
        git_dir: Path to the .git directory.

    Returns:
        Branch name string or None if HEAD is detached or unreadable.
    """
    head_path = git_dir / "HEAD"

    if not head_path.is_file():
        return None

    try:
        content = head_path.read_text().strip()
        if content.startswith("ref: refs/heads/"):
            return content[len("ref: refs/heads/"):]
    except OSError as exc:
        logger.debug("Failed to read HEAD at %s: %s", head_path, exc)

    return None


def _detect_branch_strategy(git_dir: Path) -> Dict[str, Any]:
    """Detect branch strategy from branch name patterns in refs.

    Looks at refs/heads/ (local branches) and refs/remotes/ (remote tracking).

    Strategy detection:
    - gitflow: has develop + release/* or hotfix/* branches
    - trunk-based: only main/master, no long-lived feature branches
    - github-flow: main/master + feature/* or short-lived branches
    - unknown: cannot determine

    Args:
        git_dir: Path to the .git directory.

    Returns:
        Dict with 'detected', 'pattern', and 'indicators'.
    """
    result: Dict[str, Any] = {
        "detected": False,
        "pattern": None,
        "indicators": [],
    }

    branches: List[str] = []

    # Collect local branches
    refs_heads = git_dir / "refs" / "heads"
    if refs_heads.is_dir():
        branches.extend(_collect_branch_names(refs_heads, ""))

    # Collect remote branches
    refs_remotes = git_dir / "refs" / "remotes"
    if refs_remotes.is_dir():
        for remote_dir in refs_remotes.iterdir():
            if remote_dir.is_dir():
                branches.extend(
                    _collect_branch_names(remote_dir, "")
                )

    # Also check packed-refs for branches not yet unpacked
    packed_refs = git_dir / "packed-refs"
    if packed_refs.is_file():
        try:
            for line in packed_refs.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    ref = parts[1]
                    if ref.startswith("refs/heads/"):
                        branches.append(ref[len("refs/heads/"):])
                    elif ref.startswith("refs/remotes/"):
                        # Strip remote name prefix (e.g., origin/)
                        remainder = ref[len("refs/remotes/"):]
                        slash_idx = remainder.find("/")
                        if slash_idx >= 0:
                            branches.append(remainder[slash_idx + 1:])
        except OSError:
            pass

    if not branches:
        return result

    # Deduplicate
    branch_set = set(branches)
    indicators: List[str] = []

    has_develop = "develop" in branch_set or "development" in branch_set
    has_release = any(b.startswith("release/") or b.startswith("release-") for b in branch_set)
    has_hotfix = any(b.startswith("hotfix/") or b.startswith("hotfix-") for b in branch_set)
    has_feature = any(b.startswith("feature/") or b.startswith("feature-") for b in branch_set)
    has_main = "main" in branch_set or "master" in branch_set

    # Gitflow: develop + (release/* or hotfix/*)
    if has_develop and (has_release or has_hotfix):
        result["detected"] = True
        result["pattern"] = "gitflow"
        if has_develop:
            indicators.append("develop branch present")
        if has_release:
            indicators.append("release/* branches present")
        if has_hotfix:
            indicators.append("hotfix/* branches present")
        if has_feature:
            indicators.append("feature/* branches present")
        result["indicators"] = indicators
        return result

    # Trunk-based: only main/master, few or no long-lived branches
    # Heuristic: main exists, no develop, no release/*, total branches <= 3
    if has_main and not has_develop and not has_release and len(branch_set) <= 3:
        result["detected"] = True
        result["pattern"] = "trunk-based"
        indicators.append("main/master only")
        indicators.append(f"{len(branch_set)} total branches")
        result["indicators"] = indicators
        return result

    # GitHub-flow: main + feature branches, no develop
    if has_main and not has_develop and (has_feature or len(branch_set) > 3):
        result["detected"] = True
        result["pattern"] = "github-flow"
        indicators.append("main/master present")
        if has_feature:
            indicators.append("feature/* branches present")
        indicators.append(f"{len(branch_set)} total branches")
        result["indicators"] = indicators
        return result

    # Could not determine a clear pattern
    if has_main:
        result["detected"] = False
        result["pattern"] = "unknown"
        result["indicators"] = [f"{len(branch_set)} branches, pattern unclear"]

    return result


def _collect_branch_names(directory: Path, prefix: str) -> List[str]:
    """Recursively collect branch names from refs directory.

    Args:
        directory: Path to scan for ref files.
        prefix: Current path prefix for nested refs (e.g., 'feature/').

    Returns:
        List of branch name strings.
    """
    names: List[str] = []
    try:
        for entry in directory.iterdir():
            name = f"{prefix}{entry.name}" if prefix else entry.name
            if entry.is_file():
                # Skip HEAD files in remote dirs
                if entry.name != "HEAD":
                    names.append(name)
            elif entry.is_dir():
                names.extend(_collect_branch_names(entry, f"{name}/"))
    except OSError:
        pass
    return names


def _detect_monorepo(root: Path) -> Dict[str, Any]:
    """Detect monorepo workspace configuration.

    Checks for:
    - npm workspaces: 'workspaces' field in package.json
    - pnpm workspaces: pnpm-workspace.yaml
    - Turborepo: turbo.json
    - Nx: nx.json
    - Lerna: lerna.json

    Args:
        root: Project root path.

    Returns:
        Dict with 'workspace_config' (str or None).
    """
    # Check indicator files first (turbo, nx, lerna, pnpm)
    for filename, config_name in _MONOREPO_INDICATORS:
        if (root / filename).is_file():
            return {"workspace_config": config_name}

    # Check npm workspaces in package.json
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text())
            if "workspaces" in data:
                return {"workspace_config": "npm"}
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read package.json for workspaces: %s", exc)

    return {"workspace_config": None}


def _determine_primary_platform(
    remotes: List[Dict[str, Any]],
) -> Optional[str]:
    """Determine the primary platform from the list of remotes.

    Priority: origin remote first, then first remote with a known platform.

    Args:
        remotes: List of remote dicts with 'name', 'url', 'platform'.

    Returns:
        Platform string or None.
    """
    if not remotes:
        return None

    # Prefer origin
    for remote in remotes:
        if remote.get("name") == "origin" and remote.get("platform"):
            return remote["platform"]

    # Fall back to first remote with a known platform
    for remote in remotes:
        if remote.get("platform"):
            return remote["platform"]

    return None


class GitScanner(BaseScanner):
    """Scanner for git repository configuration.

    Detects platform, remotes, default branch, branch strategy, and
    monorepo workspace configuration. Returns the `git` section per
    data-model.md section 2.5.

    Always returns a git section even when no .git directory exists
    (platform=null, remotes=[]).
    """

    @property
    def SCANNER_NAME(self) -> str:
        return "git"

    @property
    def SCANNER_VERSION(self) -> str:
        return "1.0.0"

    @property
    def OWNED_SECTIONS(self) -> List[str]:
        return ["git"]

    def scan(self, root: Path) -> ScanResult:
        """Scan the project for git configuration.

        In multi-repo mode (workspace_info.is_multi_repo), scans ALL
        subdirectories with .git and produces a 'repos' array. In
        single-repo mode, behaves as before.

        Args:
            root: Absolute path to the project root directory.

        Returns:
            ScanResult with the 'git' section populated.
        """
        start_ms = time.monotonic() * 1000
        warnings: List[str] = []

        # Multi-repo mode: scan all repo subdirectories
        if self.workspace_info and self.workspace_info.is_multi_repo:
            section = self._scan_multi_repo(root, warnings)
            elapsed = (time.monotonic() * 1000) - start_ms
            return self.make_result(
                sections={"git": section},
                warnings=warnings,
                duration_ms=elapsed,
            )

        # Single-repo mode (original behavior)
        git_dir = root / ".git"
        git_root = root

        if not git_dir.is_dir():
            # Look in immediate subdirectories for .git
            git_dir, git_root = self._find_git_in_subdirs(root)

        if git_dir is None:
            # No .git directory found at root or in subdirectories
            # Note: monorepo detection is owned by StackScanner, not duplicated here
            section: Dict[str, Any] = {
                "platform": None,
                "remotes": [],
                "default_branch": None,
                "branch_strategy": {
                    "detected": False,
                    "pattern": None,
                    "indicators": [],
                },
                "monorepo": {"workspace_config": None},
            }
            elapsed = (time.monotonic() * 1000) - start_ms
            return self.make_result(
                sections={"git": section},
                warnings=["No .git directory found"],
                duration_ms=elapsed,
            )

        # Track if git was found in a subdirectory
        if git_root != root:
            warnings.append(
                f".git found in subdirectory: {git_root.name}/"
            )

        # Parse remotes from .git/config
        git_config = _parse_git_config(git_dir)
        remotes = git_config["remotes"]

        # Determine primary platform
        platform = _determine_primary_platform(remotes)

        # Detect default branch from HEAD
        default_branch = _detect_default_branch(git_dir)

        # Detect branch strategy
        branch_strategy = _detect_branch_strategy(git_dir)

        # Note: monorepo detection is owned by StackScanner, not duplicated here
        section: Dict[str, Any] = {
            "platform": platform,
            "remotes": remotes,
            "default_branch": default_branch,
            "branch_strategy": branch_strategy,
            "monorepo": {"workspace_config": None},
        }

        # Include git_root when it differs from the scan root
        if git_root != root:
            section["git_root"] = str(git_root.relative_to(root))

        elapsed = (time.monotonic() * 1000) - start_ms
        return self.make_result(
            sections={"git": section},
            warnings=warnings,
            duration_ms=elapsed,
        )

    def _scan_multi_repo(
        self, root: Path, warnings: List[str]
    ) -> Dict[str, Any]:
        """Scan all repos in a multi-repo workspace.

        Produces a section with 'repos' array where each entry has:
        name, path, remote_url, platform, default_branch.

        Also determines the primary platform from the first repo's origin.

        Args:
            root: Workspace root path.
            warnings: Warning accumulator.

        Returns:
            Git section dict with 'repos' array and aggregate fields.
        """
        repos: List[Dict[str, Any]] = []
        primary_platform: Optional[str] = None

        for repo_dir in self.workspace_info.repo_dirs:
            git_dir = repo_dir / ".git"
            if not git_dir.is_dir():
                continue

            git_config = _parse_git_config(git_dir)
            remotes = git_config["remotes"]
            platform = _determine_primary_platform(remotes)
            default_branch = _detect_default_branch(git_dir)

            # Get origin remote URL
            origin_url = None
            for remote in remotes:
                if remote.get("name") == "origin":
                    origin_url = remote.get("url")
                    break
            if origin_url is None and remotes:
                origin_url = remotes[0].get("url")

            repo_entry: Dict[str, Any] = {
                "name": repo_dir.name,
                "path": str(repo_dir.relative_to(root)),
                "remote_url": origin_url,
                "platform": platform,
                "default_branch": default_branch,
            }
            repos.append(repo_entry)

            if primary_platform is None and platform:
                primary_platform = platform

        return {
            "platform": primary_platform,
            "workspace_type": "multi-repo",
            "repos": repos,
            "branch_strategy": {
                "detected": False,
                "pattern": None,
                "indicators": ["multi-repo workspace — per-repo strategies vary"],
            },
            "monorepo": {"workspace_config": None},
        }

    @staticmethod
    def _find_git_in_subdirs(
        root: Path,
    ) -> Tuple[Optional[Path], Path]:
        """Look for .git in immediate subdirectories.

        Args:
            root: Scan root directory.

        Returns:
            Tuple of (git_dir, git_root) if found, or (None, root) if not.
        """
        try:
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name in ("node_modules", "vendor", "__pycache__"):
                    continue
                candidate = entry / ".git"
                if candidate.is_dir():
                    return candidate, entry
        except OSError:
            pass
        return None, root


# Module-level convenience for T009 task verify command compatibility
SCANNER_NAME = GitScanner.SCANNER_NAME.fget(GitScanner())  # type: ignore[union-attr]
SCANNER_VERSION = GitScanner.SCANNER_VERSION.fget(GitScanner())  # type: ignore[union-attr]
OWNED_SECTIONS = GitScanner.OWNED_SECTIONS.fget(GitScanner())  # type: ignore[union-attr]


def scan(root: Path) -> Dict[str, Any]:
    """Module-level scan function for backward compatibility with T009 verify.

    Args:
        root: Absolute path to the project root directory.

    Returns:
        Dict mapping section names to section data.
    """
    scanner = GitScanner()
    result = scanner.scan(root)
    return result.sections
