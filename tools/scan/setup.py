"""
Setup / Installation Functions for gaia-scan

Ported from the original gaia-init — provides all the installation and setup
functionality that gaia-scan needs when operating on a fresh project
(Mode 1) or refreshing an existing project (Mode 2).

Functions:
- create_claude_directory: mkdir .claude/ with symlinks and subdirs
- copy_claude_md: deprecated no-op (identity now via submit hook)
- copy_settings_json: copy settings.template.json (always replaces)
- install_git_hooks: copy commit-msg hook to all git repos
- generate_governance: interpolate governance.template.md
- ensure_gaia_ops_package: npm install @jaguilar87/gaia-ops
- ensure_claude_code: check/install claude CLI
- generate_project_context: create/merge project-context.json
"""

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _find_package_root() -> Path:
    """Find the gaia-ops plugin root directory.

    Returns the directory containing this file's grandparent (tools/scan/setup.py
    -> tools/ -> plugin root). This works both when running from the plugin
    directory directly and when installed as a package.
    """
    return Path(__file__).resolve().parent.parent.parent


def _find_installed_package_root(project_root: Path) -> Optional[Path]:
    """Find the installed @jaguilar87/gaia-ops package in node_modules.

    Args:
        project_root: Project root directory.

    Returns:
        Path to the package root, or None if not found.
    """
    pkg_path = project_root / "node_modules" / "@jaguilar87" / "gaia-ops"
    if pkg_path.is_dir():
        return pkg_path
    return None


def _get_template_path(name: str) -> Path:
    """Get the path to a template file.

    Args:
        name: Template filename (e.g., 'settings.template.json').

    Returns:
        Absolute path to the template file.
    """
    return _find_package_root() / "templates" / name


def ensure_gaia_ops_package(project_root: Path) -> bool:
    """Ensure @jaguilar87/gaia-ops is installed as npm dependency.

    Checks node_modules for the package. If not found, creates package.json
    if needed and runs npm install.

    Args:
        project_root: Project root directory.

    Returns:
        True if package is available (already installed or newly installed).
    """
    pkg_path = project_root / "node_modules" / "@jaguilar87" / "gaia-ops" / "package.json"
    if pkg_path.is_file():
        logger.info("@jaguilar87/gaia-ops already installed")
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
            ["npm", "install", "@jaguilar87/gaia-ops"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        logger.info("@jaguilar87/gaia-ops installed")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("Failed to install @jaguilar87/gaia-ops: %s", exc)
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
    - Symlinks: agents, tools, hooks, commands, templates, config, speckit, skills, CHANGELOG.md
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
        "templates", "config", "speckit", "skills",
    ]
    created = []

    for name in symlink_names:
        link_path = claude_dir / name
        target = os.path.join(rel_path, name)

        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()

        try:
            os.symlink(target, str(link_path))
            created.append(name)
        except OSError as exc:
            logger.warning("Failed to create symlink %s: %s", name, exc)

    # CHANGELOG.md symlink
    changelog_link = claude_dir / "CHANGELOG.md"
    if changelog_link.exists() or changelog_link.is_symlink():
        changelog_link.unlink()
    try:
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

    Orchestrator identity is now injected by the UserPromptSubmit hook
    via ops_identity.py + deterministic surface routing + on-demand skills (agent-response).
    This avoids two sources of truth.

    Kept as no-op for backward compatibility with callers.
    """
    logger.info("copy_claude_md skipped — identity now injected via submit hook")
    return True


def copy_settings_json(project_root: Path) -> bool:
    """Copy settings.template.json to .claude/settings.json.

    Always overwrites -- settings.json is a system template that changes
    with each version. The template is the source of truth.

    Args:
        project_root: Project root directory.

    Returns:
        True if file was written successfully.
    """
    template_path = _get_template_path("settings.template.json")
    dest_path = project_root / ".claude" / "settings.json"

    if not template_path.is_file():
        logger.warning("settings.template.json not found at %s", template_path)
        return False

    try:
        shutil.copy2(str(template_path), str(dest_path))
        logger.info("settings.json updated from template")
        return True
    except OSError as exc:
        logger.error("Failed to write settings.json: %s", exc)
        return False


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


def generate_governance(project_root: Path, config: Dict[str, Any]) -> bool:
    """Generate governance.md from template with config interpolation.

    Only creates governance.md if it does not already exist (it is managed
    by speckit.init after initial creation).

    Args:
        project_root: Project root directory.
        config: Configuration dict with keys: cloud_provider, region,
                project_id, cluster_name, gitops, terraform.

    Returns:
        True if governance.md was created or already exists.
    """
    speckit_root = config.get("speckit_root", ".claude/project-context/speckit-project-specs")

    if os.path.isabs(speckit_root):
        resolved_root = Path(speckit_root)
    else:
        resolved_root = project_root / speckit_root

    resolved_root.mkdir(parents=True, exist_ok=True)
    dest_path = resolved_root / "governance.md"

    if dest_path.is_file():
        logger.info("governance.md already exists -- skipping (managed by speckit.init)")
        return True

    template_path = _get_template_path("governance.template.md")
    if not template_path.is_file():
        logger.warning("governance.template.md not found -- skipping")
        return False

    try:
        template = template_path.read_text()

        cloud_provider = config.get("cloud_provider", "gcp")
        k8s_platform = {
            "aws": "EKS",
            "gcp": "GKE",
        }.get(cloud_provider, "Kubernetes")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        interpolated = (
            template
            .replace("[CLOUD_PROVIDER]", (cloud_provider or "gcp").upper())
            .replace("[PRIMARY_REGION]", config.get("region", "") or "N/A")
            .replace("[PROJECT_ID]", config.get("project_id", "") or "N/A")
            .replace("[CLUSTER_NAME]", config.get("cluster_name", "") or "N/A")
            .replace("[GITOPS_PATH]", config.get("gitops", "") or "N/A")
            .replace("[TERRAFORM_PATH]", config.get("terraform", "") or "N/A")
            .replace("[POSTGRES_INSTANCE]", "N/A")
            .replace("[CONTAINER_REGISTRY]", "N/A")
            .replace("[K8S_PLATFORM]", k8s_platform)
            .replace("[DATE]", today)
        )

        dest_path.write_text(interpolated)
        logger.info("governance.md created at %s", dest_path)
        return True

    except OSError as exc:
        logger.error("Failed to create governance.md: %s", exc)
        return False


def generate_project_context(
    project_root: Path,
    config: Dict[str, Any],
    scan_context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Generate or merge project-context.json from config and scan results.

    For fresh projects (no existing file): writes a full generated context
    that includes scan results if available.

    For existing projects: merges metadata and paths from scan, preserves
    agent-enriched sections.

    Args:
        project_root: Project root directory.
        config: Configuration dict with detected/user-provided values.
        scan_context: Full context from scan orchestrator (if available).

    Returns:
        True if file was written successfully.
    """
    dest_path = (
        project_root / ".claude" / "project-context" / "project-context.json"
    )
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(timezone.utc).isoformat()

    # If we have scan_context from the orchestrator, it already has the
    # correct v2 schema structure. Use it as the base.
    if scan_context:
        # Enrich scan context with user-provided config values
        context = _enrich_scan_context(scan_context, config, now_iso, project_root)
    else:
        # Build a minimal context from config alone
        context = _build_minimal_context(config, now_iso, project_root)

    try:
        if not dest_path.is_file():
            # First-time install: write the full context
            dest_path.write_text(json.dumps(context, indent=2) + "\n")
            logger.info("project-context.json generated")
            return True

        # File exists -- merge
        try:
            existing = json.loads(dest_path.read_text())
        except (json.JSONDecodeError, OSError):
            dest_path.write_text(json.dumps(context, indent=2) + "\n")
            logger.info("project-context.json regenerated (previous was invalid)")
            return True

        merged = _merge_project_context(existing, context)
        dest_path.write_text(json.dumps(merged, indent=2) + "\n")
        logger.info("project-context.json updated (metadata+paths synced, sections preserved)")
        return True

    except OSError as exc:
        logger.error("Failed to write project-context.json: %s", exc)
        return False


def _enrich_scan_context(
    scan_context: Dict[str, Any],
    config: Dict[str, Any],
    now_iso: str,
    project_root: Path,
) -> Dict[str, Any]:
    """Enrich scan context with user-provided config values."""
    import copy
    context = copy.deepcopy(scan_context)

    # Ensure metadata exists
    meta = context.setdefault("metadata", {})
    meta["version"] = meta.get("version", "2.0")
    meta["last_updated"] = now_iso
    meta["created_by"] = "gaia-scan"

    # Update infrastructure.paths from config (user overrides trump scan)
    sections = context.setdefault("sections", {})
    infra = sections.setdefault("infrastructure", {})
    infra_paths = infra.setdefault("paths", {})
    if config.get("gitops"):
        infra_paths["gitops"] = config["gitops"]
    if config.get("terraform"):
        infra_paths["terraform"] = config["terraform"]
    if config.get("app_services"):
        infra_paths["app_services"] = config["app_services"]
    # Remove top-level paths if present (single source: infrastructure.paths)
    context.pop("paths", None)

    # Ensure operational_guidelines has speckit_root
    sections = context.setdefault("sections", {})
    op_guide = sections.setdefault("operational_guidelines", {})
    if "speckit_root" not in op_guide:
        op_guide["speckit_root"] = config.get(
            "speckit_root",
            ".claude/project-context/speckit-project-specs",
        )

    # Enrich sections from contract file
    _enrich_from_contracts(context, config, project_root)

    return context


def _build_minimal_context(
    config: Dict[str, Any],
    now_iso: str,
    project_root: Path,
) -> Dict[str, Any]:
    """Build a minimal project-context.json from config when no scan data available."""
    cloud_provider = config.get("cloud_provider", "gcp")
    project_name = config.get("project_name", project_root.name)

    metadata = {
        "version": "2.0",
        "last_updated": now_iso,
        "project_name": project_name,
        "project_root": ".",
        "created_by": "gaia-scan",
        "cloud_provider": cloud_provider,
        "environment": "non-prod",
        "primary_region": config.get("region", ""),
    }

    if cloud_provider in ("gcp", "multi-cloud") and config.get("project_id"):
        metadata["project_id"] = config["project_id"]
    if cloud_provider in ("aws", "multi-cloud") and config.get("project_id"):
        metadata["aws_account"] = config["project_id"]

    cloud_entry: Dict[str, Any] = {
        "name": cloud_provider,
        "region": config.get("region", ""),
    }
    if cloud_provider in ("gcp", "multi-cloud") and config.get("project_id"):
        cloud_entry["project_id"] = config["project_id"]
    if cloud_provider in ("aws", "multi-cloud") and config.get("project_id"):
        cloud_entry["account_id"] = config["project_id"]

    speckit_root = config.get("speckit_root", ".claude/project-context/speckit-project-specs")

    # Build paths dict, filtering out empty strings
    infra_paths: Dict[str, str] = {}
    for key in ("gitops", "terraform", "app_services"):
        val = config.get(key, "")
        if val:
            infra_paths[key] = val

    context = {
        "metadata": metadata,
        "sections": {
            "project_identity": {
                "name": project_name,
                "type": "application",
            },
            "stack": {"languages": [], "frameworks": [], "build_tools": []},
            "git": {
                "platform": config.get("git_platform"),
                "remotes": [],
                "default_branch": "main",
            },
            "environment": {"runtimes": [], "os": {}},
            "infrastructure": {
                "cloud_providers": [cloud_entry],
                "ci_cd": (
                    [{"platform": config["ci_platform"]}]
                    if config.get("ci_platform")
                    else []
                ),
                "paths": infra_paths,
            },
            "operational_guidelines": {
                "speckit_root": speckit_root,
                "commit_standards": {
                    "format": "conventional_commits",
                    "validation_required": True,
                    "config_path": ".claude/config/git_standards.json",
                },
            },
        },
    }

    _enrich_from_contracts(context, config, project_root)
    return context


def _enrich_from_contracts(
    context: Dict[str, Any],
    config: Dict[str, Any],
    project_root: Path,
) -> None:
    """Enrich context sections from contract file (progressive context enrichment).

    Only creates empty {} placeholders for scanner-owned sections that agents
    need to read. Agent-enriched and mixed sections are NOT pre-created --
    they should only exist when populated with actual data. The exception is
    architecture_overview, which always exists (even empty) because all agent
    contracts reference it.
    """
    try:
        cloud_provider = config.get("cloud_provider", "gcp")
        provider = "gcp" if cloud_provider == "multi-cloud" else cloud_provider
        contract_path = _find_package_root() / "config" / f"context-contracts.{provider}.json"

        if not contract_path.is_file():
            return

        contracts = json.loads(contract_path.read_text())
        contract_sections: set = set()
        for agent in (contracts.get("agents") or {}).values():
            for s in agent.get("read", []):
                contract_sections.add(s)
            for s in agent.get("write", []):
                contract_sections.add(s)

        # Sections that should NOT be pre-created as empty {}.
        # They only exist when an agent or scanner populates them with data.
        # architecture_overview is the exception -- always present.
        from tools.scan.merge import AGENT_ENRICHED_SECTIONS, MIXED_SECTION_SCANNER_FIELDS
        skip_empty = (
            AGENT_ENRICHED_SECTIONS
            | frozenset(MIXED_SECTION_SCANNER_FIELDS.keys())
        ) - {"architecture_overview"}

        sections = context.setdefault("sections", {})
        for section in contract_sections:
            if section not in sections:
                if section in skip_empty:
                    continue
                sections[section] = {}

    except (json.JSONDecodeError, OSError):
        pass


def _merge_project_context(
    existing: Dict[str, Any],
    new_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge new context into existing, preserving agent-enriched sections.

    Strategy:
    - metadata: field-by-field replace from new
    - paths: field-by-field replace from new
    - sections: preserve existing content; add new sections if absent
    """
    import copy

    merged = {
        "metadata": {
            **(existing.get("metadata") or {}),
            **(new_context.get("metadata") or {}),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        },
        "sections": {
            # Start from new context sections as schema base,
            # then override with existing sections that have content
            **(new_context.get("sections") or {}),
            **{
                k: v
                for k, v in (existing.get("sections") or {}).items()
                if v is not None and isinstance(v, dict) and len(v) > 0
            },
        },
    }

    return merged
