"""
Setup / Installation Functions for gaia-scan

Provides installation and setup functionality when operating on a fresh
project (Mode 1) or refreshing an existing project (Mode 2).

Functions:
- create_claude_directory: mkdir .claude/ with symlinks and subdirs
- copy_claude_md: deprecated no-op (identity now via submit hook)
- copy_settings_json: create minimal settings.json only if missing (non-invasive)
- install_git_hooks: copy commit-msg hook to all git repos
- ensure_gaia_ops_package: npm install @jaguilar87/gaia
- ensure_claude_code: check/install claude CLI
"""

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Windows detection: junctions don't require admin privileges
_IS_WINDOWS = platform.system() == "Windows"


def _create_dir_link(target: str, link: str) -> None:
    """Create a directory link: junction on Windows, symlink on Unix.

    Windows junctions don't require admin/developer-mode privileges,
    unlike directory symlinks.  The target must be an absolute path
    on Windows (junctions don't support relative targets).
    """
    if _IS_WINDOWS:
        import _winapi  # stdlib on Windows, unavailable elsewhere
        _winapi.CreateJunction(target, link)
    else:
        os.symlink(target, link)


def _find_package_root() -> Path:
    """Find the gaia-ops plugin root directory.

    Returns the directory containing this file's grandparent (tools/scan/setup.py
    -> tools/ -> plugin root). This works both when running from the plugin
    directory directly and when installed as a package.
    """
    return Path(__file__).resolve().parent.parent.parent


def _find_installed_package_root(project_root: Path) -> Optional[Path]:
    """Find the installed @jaguilar87/gaia package in node_modules.

    Args:
        project_root: Project root directory.

    Returns:
        Path to the package root, or None if not found.
    """
    pkg_path = project_root / "node_modules" / "@jaguilar87" / "gaia"
    if pkg_path.is_dir():
        return pkg_path
    return None


def ensure_gaia_ops_package(project_root: Path) -> bool:
    """Ensure @jaguilar87/gaia is installed as npm dependency.

    Checks node_modules for the package. If not found, creates package.json
    if needed and runs npm install.

    Args:
        project_root: Project root directory.

    Returns:
        True if package is available (already installed or newly installed).
    """
    pkg_path = project_root / "node_modules" / "@jaguilar87" / "gaia" / "package.json"
    if pkg_path.is_file():
        logger.info("@jaguilar87/gaia already installed")
        return True

    # Create package.json if missing
    package_json_path = project_root / "package.json"
    if not package_json_path.is_file():
        initial_pkg = {
            "name": "my-project",
            "version": "1.0.0",
            "private": True,
            "dependencies": {},
        }
        package_json_path.write_text(json.dumps(initial_pkg, indent=2))

    try:
        subprocess.run(
            ["npm", "install", "@jaguilar87/gaia"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        logger.info("@jaguilar87/gaia installed")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("Failed to install @jaguilar87/gaia: %s", exc)
        return False


def ensure_claude_code(skip_install: bool = False) -> Dict[str, Any]:
    """Check if Claude Code CLI is installed, optionally install it.

    Args:
        skip_install: If True, skip installation attempt.

    Returns:
        Dict with 'installed' (bool) and 'version' (str or None).
    """
    # Try to get version
    for cmd in ["claude --version", "claude-code --version"]:
        try:
            result = subprocess.run(
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split("\n")[0]
                return {"installed": True, "version": version}
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    if skip_install:
        logger.warning("Claude Code not installed (--skip-claude-install used)")
        return {"installed": False, "version": None}

    # Attempt installation
    try:
        subprocess.run(
            ["npm", "install", "-g", "@anthropic-ai/claude-code"],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        logger.info("Claude Code installed")
        return {"installed": True, "version": "newly installed"}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("Failed to install Claude Code: %s", exc)
        return {"installed": False, "version": None}


def create_claude_directory(project_root: Path) -> List[str]:
    """Create .claude/ directory with symlinks to the gaia-ops package.

    Creates:
    - Symlinks: agents, tools, hooks, commands, templates, config, skills, CHANGELOG.md
    - Directories: logs, tests, project-context, project-context/workflow-episodic-memory, approvals

    Args:
        project_root: Project root directory.

    Returns:
        List of created symlink names (for reporting).
    """
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(exist_ok=True)

    # Find the installed package for symlinks
    package_path = _find_installed_package_root(project_root)
    if package_path is None:
        # Fallback: use the plugin root directly (running from source)
        package_path = _find_package_root()

    # Compute relative path from .claude/ to the package
    try:
        rel_path = os.path.relpath(str(package_path), str(claude_dir))
    except ValueError:
        # On Windows, relpath can fail across drives
        rel_path = str(package_path)

    # Create symlinks
    symlink_names = [
        "agents", "tools", "hooks", "commands",
        "templates", "config", "skills",
    ]
    created = []

    for name in symlink_names:
        link_path = claude_dir / name
        # Junctions on Windows require absolute targets; symlinks on Unix use relative
        if _IS_WINDOWS:
            target = str(package_path / name)
        else:
            target = os.path.join(rel_path, name)

        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()

        try:
            _create_dir_link(target, str(link_path))
            created.append(name)
        except OSError as exc:
            logger.warning("Failed to create symlink %s: %s", name, exc)

    # CHANGELOG.md symlink (file, not directory — junctions only work for dirs)
    changelog_link = claude_dir / "CHANGELOG.md"
    if changelog_link.exists() or changelog_link.is_symlink():
        changelog_link.unlink()
    try:
        if _IS_WINDOWS:
            # File symlinks need admin on Windows; copy instead
            shutil.copy2(str(package_path / "CHANGELOG.md"), str(changelog_link))
        else:
            os.symlink(os.path.join(rel_path, "CHANGELOG.md"), str(changelog_link))
        created.append("CHANGELOG.md")
    except OSError as exc:
        logger.warning("Failed to create CHANGELOG.md symlink: %s", exc)

    # Create project-specific directories (NOT symlinked)
    for subdir in [
        "logs",
        "tests",
        "project-context",
        os.path.join("project-context", "workflow-episodic-memory"),
        "approvals",
    ]:
        (claude_dir / subdir).mkdir(parents=True, exist_ok=True)

    return created


def copy_claude_md(project_root: Path) -> bool:
    """Deprecated — CLAUDE.md is no longer generated from template.

    Orchestrator identity lives in agents/gaia-orchestrator.md, activated via
    settings.local.json agent field.

    Kept as no-op for backward compatibility with callers.
    """
    logger.info("copy_claude_md skipped — identity now injected via submit hook")
    return True


def copy_settings_json(project_root: Path) -> bool:
    """Create a minimal .claude/settings.json only if it does not exist.

    Non-invasive: never overwrites an existing settings.json.  Hooks are
    provided by hooks.json (auto-discovered via the .claude/hooks symlink).
    Env vars and permissions live in settings.local.json.

    Args:
        project_root: Project root directory.

    Returns:
        True if file exists (created or already present).
    """
    dest_path = project_root / ".claude" / "settings.json"

    if dest_path.is_file():
        logger.info("settings.json already exists — not overwriting")
        return True

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text("{}\n")
        logger.info("settings.json created (minimal — hooks from hooks.json, env from settings.local.json)")
        return True
    except OSError as exc:
        logger.error("Failed to write settings.json: %s", exc)
        return False


def merge_hooks_to_settings_local(project_root: Path) -> bool:
    """Merge hooks from hooks.json into .claude/settings.local.json.

    Thin wrapper around the canonical implementation in
    ``cli._install_helpers.merge_local_hooks``. Kept for backward compatibility
    with existing call sites in ``bin/cli/scan.py``.

    The canonical implementation:
      - Uses absolute paths (resolved via .claude/hooks) so hooks fire
        regardless of cwd at execution time.
      - Migrates legacy relative ``.claude/hooks/...`` paths to absolute on
        the fly, so workspaces installed with older gaia versions converge
        automatically.
      - Returns a structured result dict; this wrapper translates back to the
        legacy bool API ("True if settings.local.json was modified").
      - Auto-resolves the plugin root via the installed npm package first,
        falling back to the source tree.

    Args:
        project_root: Project root directory.

    Returns:
        True if settings.local.json was modified.
    """
    # Resolve plugin root with the same precedence the old implementation used:
    # installed npm package first, then source tree.
    pkg_root = _find_installed_package_root(project_root) or _find_package_root()

    # Import lazily to avoid circular dependencies with bin/cli at module load.
    import sys
    _bin_dir = _find_package_root() / "bin"
    if str(_bin_dir) not in sys.path:
        sys.path.insert(0, str(_bin_dir))
    from cli._install_helpers import merge_local_hooks  # noqa: E402

    result = merge_local_hooks(project_root, plugin_root=pkg_root)
    action = result.get("action") if isinstance(result, dict) else None
    return action == "updated"


def install_git_hooks(project_root: Path) -> int:
    """Install commit-msg git hook to all detected git repositories.

    Copies git-hooks/commit-msg from the package to .git/hooks/ in all
    repos found in the project root and its immediate subdirectories.

    Args:
        project_root: Project root directory.

    Returns:
        Number of repos where hooks were installed.
    """
    hook_source = _find_package_root() / "git-hooks" / "commit-msg"
    if not hook_source.is_file():
        logger.warning("git-hooks/commit-msg not found in package, skipping")
        return 0

    # Find git repos: project root and immediate subdirectories
    candidates = [project_root]
    try:
        for entry in project_root.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and entry.name != "node_modules":
                candidates.append(entry)
    except OSError:
        pass

    installed = 0
    for dir_path in candidates:
        git_hooks_dir = dir_path / ".git" / "hooks"
        if not git_hooks_dir.is_dir():
            continue

        dest = git_hooks_dir / "commit-msg"
        try:
            shutil.copy2(str(hook_source), str(dest))
            os.chmod(str(dest), 0o755)
            installed += 1
        except OSError as exc:
            logger.warning("Failed to install hook in %s: %s", dir_path, exc)

    return installed


