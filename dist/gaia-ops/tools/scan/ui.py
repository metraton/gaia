"""
Rail UI for gaia-scan

Clack-style rail output for scan results. Zero prompts. Fully automatic.
All output goes to stderr. stdout is reserved for JSON only.

Classes:
- RailUI: Clack-style rail output renderer

Functions:
- format_scanner_results: Transform raw scan output into display sections
"""

import os
import sys
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stderr, "isatty"):
        return False
    return sys.stderr.isatty()


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    """Apply ANSI color code if color is supported."""
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _cyan(text: str) -> str:
    return _c("36", text)


def _green(text: str) -> str:
    return _c("32", text)


def _yellow(text: str) -> str:
    return _c("33", text)


def _dim(text: str) -> str:
    return _c("2", text)


def _bold(text: str) -> str:
    return _c("1", text)


# ---------------------------------------------------------------------------
# Rail UI
# ---------------------------------------------------------------------------

class RailUI:
    """Clack-style rail output for scan results.

    All output goes to stderr. The rail character is dimmed.

    Args:
        version: Scanner version string for the header.
        color: Whether to use ANSI colors (overrides auto-detect).
    """

    def __init__(self, version: str, color: Optional[bool] = None):
        self.version = version
        self._color = color if color is not None else _COLOR

    def _c(self, code: str, text: str) -> str:
        """Apply ANSI color code if color is enabled for this instance."""
        return f"\033[{code}m{text}\033[0m" if self._color else text

    def _cyan(self, text: str) -> str:
        return self._c("36", text)

    def _green(self, text: str) -> str:
        return self._c("32", text)

    def _yellow(self, text: str) -> str:
        return self._c("33", text)

    def _dim(self, text: str) -> str:
        return self._c("2", text)

    def _bold(self, text: str) -> str:
        return self._c("1", text)

    def _rail(self) -> str:
        """Return the dimmed rail character."""
        return self._dim("\u2502")

    def _write(self, text: str) -> None:
        """Write a line to stderr."""
        print(text, file=sys.stderr)

    def start(self) -> None:
        """Print the header: top-left corner + version."""
        self._write(self._cyan(f"\u250c  gaia-scan v{self.version}"))
        self._write(self._rail())

    def scanning(self) -> None:
        """Print the scanning indicator."""
        self._write(self._cyan(f"\u25d2  Scanning..."))
        self._write(self._rail())

    def section(self, name: str, lines: List[str]) -> None:
        """Print a section with its detail lines.

        Args:
            name: Section title (e.g. "Stack", "Infrastructure").
            lines: Detail lines to display under the section.
        """
        self._write(f"{self._green('\u25c7')}  {self._cyan(name)}")
        for line in lines:
            self._write(f"{self._rail()}  {line}")
        self._write(self._rail())

    def section_compact(self, names: List[str]) -> None:
        """Print multiple section names on a single line (scan-only mode).

        Args:
            names: List of section names to join with middle-dot.
        """
        joined = self._cyan(" \u00b7 ".join(names))
        self._write(f"{self._green('\u25c7')}  {joined}")
        self._write(self._rail())

    def warning(self, count: int, messages: List[str]) -> None:
        """Print warnings section.

        Args:
            count: Total number of warnings.
            messages: Warning messages to display.
        """
        self._write(f"{self._yellow('\u26a0')}  {self._yellow(f'Warnings ({count})')}")
        for msg in messages:
            self._write(f"{self._rail()}  {msg}")
        self._write(self._rail())

    def done(self, duration_s: float, suffix: str = "") -> None:
        """Print the done marker with duration.

        Args:
            duration_s: Scan duration in seconds.
            suffix: Optional text to append after duration.
        """
        text = f"\u25c6  Done in {duration_s:.1f}s"
        if suffix:
            text += f" \u00b7 {suffix}"
        self._write(self._green(text))
        self._write(self._rail())

    def created(self, items: Dict[str, str]) -> None:
        """Print the 'Created:' summary for fresh installs.

        Args:
            items: Dict of {name: description} for created items.
        """
        self._write(f"{self._rail()}  Created:")
        for name, desc in items.items():
            self._write(f"{self._rail()}    {name:<18s} {self._dim(desc)}")
        self._write(self._rail())

    def updated(self, sections_updated: int, sections_preserved: int) -> None:
        """Print the 'Updated/Preserved' summary for rescans.

        Args:
            sections_updated: Number of scanner-updated sections.
            sections_preserved: Number of agent-enriched preserved sections.
        """
        self._write(f"{self._rail()}  Updated: {sections_updated} sections")
        self._write(f"{self._rail()}  Preserved: {sections_preserved} agent-enriched sections")
        self._write(f"{self._rail()}  Synced: settings.json, settings.local.json")
        self._write(self._rail())

    def footer(self, message: str) -> None:
        """Print the footer with closing rail corner.

        Args:
            message: Footer message text.
        """
        self._write(f"{self._dim('\u2514')}  {message}")


# ---------------------------------------------------------------------------
# Format scanner results for display
# ---------------------------------------------------------------------------

def format_scanner_results(output: Any, project_root: Any = None) -> List[Dict[str, Any]]:
    """Transform raw scan output into display sections for RailUI.

    Produces a project-aware context summary with:
    - Project section(s): identity line + infrastructure line
    - Tools section: detected CLI tools
    - Runtime section: language runtimes + OS

    Args:
        output: ScanOutput from the orchestrator (has .context, .scanner_results).
        project_root: Path to the project root (used for fallback project name).

    Returns:
        List of dicts with 'name' and 'lines' keys.
    """
    from pathlib import Path

    ctx = output.context
    scan_sections = ctx.get("sections", {})
    root = Path(project_root) if project_root else None

    sections: List[Dict[str, Any]] = []

    # --- Project section(s) ---
    project_sections = _build_project_sections(scan_sections, root)
    sections.extend(project_sections)

    # --- Tools ---
    env = scan_sections.get("environment", {})
    tool_list = env.get("tools", [])
    if tool_list:
        count = len(tool_list)
        names = [t.get("name", "") for t in tool_list if isinstance(t, dict)]
        # Show first 6 tools + ellipsis if more
        if len(names) > 6:
            display = " \u00b7 ".join(names[:6]) + " ..."
        else:
            display = " \u00b7 ".join(names)
        sections.append({"name": f"Tools ({count})", "lines": [display]})

    # --- Runtime ---
    os_info = env.get("os", {})
    runtimes = env.get("runtimes", [])

    rt_parts = []
    for rt in runtimes:
        name = _capitalize(rt.get("name", ""))
        ver = rt.get("version", "")
        if name and ver:
            # Use major.minor for cleaner display
            parts = ver.split(".")
            short_ver = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
            rt_parts.append(f"{name} {short_ver}")

    wsl = os_info.get("wsl", False)
    if wsl:
        wsl_ver = os_info.get("wsl_version", "")
        rt_parts.append(f"WSL{wsl_ver}")
    else:
        platform = os_info.get("platform", "")
        if platform:
            rt_parts.append(_capitalize(platform))

    if rt_parts:
        sections.append({"name": "Runtime", "lines": [" \u00b7 ".join(rt_parts)]})

    return sections


def _build_project_sections(
    scan_sections: Dict[str, Any],
    project_root: Any,
) -> List[Dict[str, Any]]:
    """Build project context sections. Returns list for future multi-project support.

    Args:
        scan_sections: The 'sections' dict from scan output context.
        project_root: Path to the project root (for fallback name).

    Returns:
        List of section dicts, one per project.
    """
    projects = []
    projects.append(_build_single_project(scan_sections, project_root))
    return projects


def _build_single_project(
    scan_sections: Dict[str, Any],
    project_root: Any,
) -> Dict[str, Any]:
    """Build a single project summary section.

    Line 1: Project type + service count + git platform
    Line 2: Cloud providers + orchestration + IaC

    Args:
        scan_sections: The 'sections' dict from scan output context.
        project_root: Path to the project root (for fallback name).

    Returns:
        Section dict with 'name' and 'lines'.
    """
    from pathlib import Path

    # --- Project name ---
    project_identity = scan_sections.get("project_identity", {})
    name = project_identity.get("name", "")
    # Fallback: if name is npm-init default or empty, use directory name
    if not name or name == "my-project":
        if project_root:
            name = Path(project_root).name
        else:
            name = "project"

    # --- Line 1: Identity ---
    line1_parts = []

    # Project type
    monorepo = project_identity.get("monorepo", {})
    proj_type = project_identity.get("type", "")
    if monorepo.get("detected") or proj_type == "monorepo":
        line1_parts.append("Monorepo")
    elif proj_type == "library":
        line1_parts.append("Library")
    else:
        line1_parts.append("Single app")

    # Service count from Dockerfiles (non-worktree)
    infra = scan_sections.get("infrastructure", {})
    service_count = _count_services(infra)
    if service_count > 0:
        if service_count >= 15:
            line1_parts.append(f"{service_count}+ services")
        else:
            line1_parts.append(f"{service_count} services")

    # Git platform
    git_platform = _detect_git_platform(scan_sections)
    if git_platform:
        line1_parts.append(git_platform)

    # --- Line 2: Infrastructure ---
    line2_parts = []

    # Cloud providers
    cloud_providers = infra.get("cloud_providers", [])
    if cloud_providers:
        cloud_names = []
        for cp in cloud_providers:
            cp_name = cp.get("name", "").upper()
            if cp_name:
                cloud_names.append(cp_name)
        if cloud_names:
            line2_parts.append(" + ".join(cloud_names))

    # Orchestration: Kubernetes + GitOps tool
    orch_summary = _build_orchestration_summary(scan_sections)
    if orch_summary:
        line2_parts.append(orch_summary)

    # IaC tools
    iac = infra.get("iac", [])
    if iac:
        iac_names = []
        for tool_entry in iac:
            tool_name = _capitalize(tool_entry.get("tool", ""))
            if tool_name and tool_name not in iac_names:
                iac_names.append(tool_name)
        if iac_names:
            line2_parts.append("/".join(iac_names))

    lines = []
    if line1_parts:
        lines.append(" \u00b7 ".join(line1_parts))
    if line2_parts:
        lines.append(" \u00b7 ".join(line2_parts))

    return {"name": name, "lines": lines}


def _count_services(infra: Dict[str, Any]) -> int:
    """Count unique services from Docker container files.

    Counts non-worktree Dockerfiles as a proxy for service count.

    Args:
        infra: Infrastructure section from scan results.

    Returns:
        Number of services detected.
    """
    containers = infra.get("containers", [])
    for ct in containers:
        if ct.get("tool") == "docker":
            files = ct.get("files", [])
            # Count non-worktree, non-template, non-example Dockerfiles
            count = sum(
                1 for f in files
                if not f.startswith("worktrees/")
                and "template" not in f.lower()
                and not f.endswith(".example")
            )
            return count
    return 0


def _detect_git_platform(scan_sections: Dict[str, Any]) -> Optional[str]:
    """Detect git platform from scan results.

    Checks git.platform first, then parses remotes, then falls back to
    tool presence (glab -> GitLab, gh -> GitHub).

    Args:
        scan_sections: The 'sections' dict from scan output.

    Returns:
        Platform name (e.g. "GitLab", "GitHub") or None.
    """
    git = scan_sections.get("git", {})

    # Direct platform detection
    platform = git.get("platform")
    if platform:
        return _capitalize_platform(platform)

    # Parse remotes for platform hints
    remotes = git.get("remotes", [])
    for remote in remotes:
        remote_platform = remote.get("platform")
        if remote_platform:
            return _capitalize_platform(remote_platform)
        url = remote.get("url", "")
        if "gitlab" in url.lower():
            return "GitLab"
        if "github" in url.lower():
            return "GitHub"

    # Fallback: check for glab or gh tool presence
    env = scan_sections.get("environment", {})
    tools = env.get("tools", [])
    tool_names = {t.get("name", "") for t in tools if isinstance(t, dict)}
    if "glab" in tool_names:
        return "GitLab"
    if "gh" in tool_names:
        return "GitHub"

    return None


def _capitalize_platform(platform: str) -> str:
    """Capitalize a git platform name.

    Args:
        platform: Raw platform string (e.g. "gitlab", "github").

    Returns:
        Human-friendly name (e.g. "GitLab", "GitHub").
    """
    platform_map = {
        "gitlab": "GitLab",
        "gitlab-ci": "GitLab",
        "github": "GitHub",
        "github-actions": "GitHub",
        "bitbucket": "Bitbucket",
    }
    return platform_map.get(platform.lower(), platform.capitalize())


def _build_orchestration_summary(scan_sections: Dict[str, Any]) -> Optional[str]:
    """Build orchestration summary string.

    Produces strings like "Kubernetes (Flux)" or "Kubernetes".

    Args:
        scan_sections: The 'sections' dict from scan output.

    Returns:
        Orchestration summary string, or None.
    """
    env = scan_sections.get("environment", {})
    tools = env.get("tools", [])
    tool_names = {t.get("name", "") for t in tools if isinstance(t, dict)}

    has_k8s = "kubectl" in tool_names
    if not has_k8s:
        return None

    # Detect GitOps tool
    gitops_tool = None
    if "flux" in tool_names or "fluxctl" in tool_names:
        gitops_tool = "Flux"

    # Check infrastructure for gitops hints (flux files, argocd, etc.)
    infra = scan_sections.get("infrastructure", {})
    ci_cd = infra.get("ci_cd", [])
    for ci in ci_cd:
        if isinstance(ci, dict):
            ci_platform = ci.get("platform", "").lower()
            if "flux" in ci_platform:
                gitops_tool = "Flux"
            elif "argo" in ci_platform or "argocd" in ci_platform:
                gitops_tool = "ArgoCD"

    # Check orchestration section if present
    orch = scan_sections.get("orchestration", {})
    if isinstance(orch, dict):
        k8s = orch.get("kubernetes", {})
        if isinstance(k8s, dict) and k8s.get("detected"):
            has_k8s = True
        gitops = orch.get("gitops", {})
        if isinstance(gitops, dict) and gitops.get("tool"):
            gitops_tool = gitops["tool"].capitalize()

    if has_k8s:
        if gitops_tool:
            return f"Kubernetes ({gitops_tool})"
        return "Kubernetes"

    return None


def collect_warnings(output: Any) -> List[str]:
    """Collect user-facing warnings from scan output.

    Args:
        output: ScanOutput from the orchestrator.

    Returns:
        List of warning message strings.
    """
    warnings = []

    # Check for no git directory
    git_section = output.context.get("sections", {}).get("git", {})
    remotes = git_section.get("remotes", [])
    if not remotes and not git_section.get("platform"):
        warnings.append("No .git directory found at project root")

    # Include scanner-level warnings (deduplicated, user-facing only)
    for w in output.warnings:
        # Skip if already covered by a more descriptive version
        if w not in warnings and not any(w in existing for existing in warnings):
            warnings.append(w)

    return warnings


def collect_created_summary(project_root: "Path", output: Any) -> Dict[str, str]:
    """Collect summary of created artifacts for fresh install display.

    Args:
        project_root: Project root directory.
        output: ScanOutput from the orchestrator.

    Returns:
        Dict of {artifact_name: description}.
    """
    from pathlib import Path

    items = {}
    claude_dir = Path(project_root) / ".claude"

    # Count symlinks
    symlink_count = 0
    if claude_dir.is_dir():
        for entry in claude_dir.iterdir():
            if entry.is_symlink():
                symlink_count += 1
    if symlink_count:
        items[".claude/"] = f"{symlink_count} symlinks"

    # CLAUDE.md
    claude_md = Path(project_root) / "CLAUDE.md"
    if claude_md.is_file():
        items["CLAUDE.md"] = "orchestrator identity"

    # settings.json
    settings = claude_dir / "settings.json"
    if settings.is_file():
        items["settings.json"] = "hooks + permissions"

    # project-context sections
    ctx_path = claude_dir / "project-context" / "project-context.json"
    if ctx_path.is_file():
        try:
            import json
            data = json.loads(ctx_path.read_text())
            section_count = len(data.get("sections", {}))
            items["project-context"] = f"{section_count} sections detected"
        except Exception:
            items["project-context"] = "generated"

    return items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capitalize(s: str) -> str:
    """Capitalize first letter, keep rest. Handle known names."""
    name_map = {
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "python": "Python",
        "go": "Go",
        "java": "Java",
        "rust": "Rust",
        "express": "Express",
        "terraform": "Terraform",
        "terragrunt": "Terragrunt",
        "docker": "Docker",
        "node": "Node",
        "python3": "Python",
        "npm": "npm",
        "pnpm": "pnpm",
        "yarn": "yarn",
        "linux": "Linux",
        "darwin": "macOS",
        "win32": "Windows",
        "terraform_provider": "Terraform",
        "cli_config": "CLI",
    }
    return name_map.get(s, s.capitalize() if s else s)


