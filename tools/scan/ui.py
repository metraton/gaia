"""
Interactive UI for gaia-scan

Provides display and prompting functions for the interactive setup flow.
Uses simple input() for prompts with ANSI colors for terminal output.

Functions:
- display_config: ASCII banner + categorized table showing detected values
- prompt_gaps: ask for missing values
- confirm_or_edit: Accept / Edit / Cancel prompt
- edit_config: full edit mode for all fields
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def _cyan(text: str) -> str:
    return f"\033[36m{text}\033[0m" if _COLOR else text


def _green(text: str) -> str:
    return f"\033[32m{text}\033[0m" if _COLOR else text


def _yellow(text: str) -> str:
    return f"\033[33m{text}\033[0m" if _COLOR else text


def _red(text: str) -> str:
    return f"\033[31m{text}\033[0m" if _COLOR else text


def _gray(text: str) -> str:
    return f"\033[90m{text}\033[0m" if _COLOR else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _COLOR else text


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER = r"""
  ██████╗  █████╗ ██╗ █████╗      ██████╗ ██████╗ ███████╗
 ██╔════╝ ██╔══██╗██║██╔══██╗    ██╔═══██╗██╔══██╗██╔════╝
 ██║  ███╗███████║██║███████║    ██║   ██║██████╔╝███████╗
 ██║   ██║██╔══██║██║██╔══██║    ██║   ██║██╔═══╝ ╚════██║
 ╚██████╔╝██║  ██║██║██║  ██║    ╚██████╔╝██║     ███████║
  ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝     ╚═════╝ ╚═╝     ╚══════╝
"""


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _print_field(label: str, value: Optional[str], found: bool) -> None:
    """Print a single config field with status indicator."""
    padded = label.ljust(14)
    if value and found:
        print(_green(f"    {padded} {value}  ✓"), file=sys.stderr)
    elif value:
        print(_yellow(f"    {padded} {value}  (will be created)"), file=sys.stderr)
    else:
        print(_yellow(f"    {padded} (not detected)  ?"), file=sys.stderr)


def display_config(config: Dict[str, Any], project_root: Path) -> List[str]:
    """Display scan results as a categorized table.

    Args:
        config: Configuration dict with all detected/user values.
        project_root: Project root for path validation.

    Returns:
        List of gap field names (missing values).
    """
    print(_cyan(BANNER), file=sys.stderr)
    print(_bold(_cyan("  Detected Configuration\n")), file=sys.stderr)

    # Paths
    print("  Paths", file=sys.stderr)
    for label, key in [("GitOps", "gitops"), ("Terraform", "terraform"), ("App Services", "app_services")]:
        val = config.get(key, "")
        exists = bool(val) and (project_root / val).exists()
        _print_field(label, val or None, exists)

    # Cloud
    print("\n  Cloud", file=sys.stderr)
    cloud = config.get("cloud_provider", "")
    _print_field("Provider", cloud.upper() if cloud else None, bool(cloud))
    _print_field("Project ID", config.get("project_id") or None, bool(config.get("project_id")))
    _print_field("Region", config.get("region") or None, bool(config.get("region")))
    _print_field("Cluster", config.get("cluster_name") or None, bool(config.get("cluster_name")))

    # Identity
    print("\n  Identity", file=sys.stderr)
    _print_field("Project Name", config.get("project_name") or None, bool(config.get("project_name")))
    _print_field("Git Platform", config.get("git_platform") or None, bool(config.get("git_platform")))
    _print_field("CI/CD", config.get("ci_platform") or None, bool(config.get("ci_platform")))

    # Git remotes
    remotes = config.get("git_remotes", [])
    if remotes:
        print("\n  Git Repositories", file=sys.stderr)
        for r in remotes[:5]:
            if isinstance(r, dict):
                path = r.get("path", r.get("name", "."))
                remote = r.get("remote", r.get("url", ""))
                print(_gray(f"    {path} -> {remote}"), file=sys.stderr)
        if len(remotes) > 5:
            print(_gray(f"    ... and {len(remotes) - 5} more"), file=sys.stderr)

    # Claude Code
    claude_code = config.get("claude_code", {})
    print("\n  Claude Code", file=sys.stderr)
    if claude_code.get("installed"):
        print(_green(f"    {claude_code.get('version', 'installed')}  ✓"), file=sys.stderr)
    else:
        print(_yellow("    Not installed (will be installed automatically)"), file=sys.stderr)

    print("", file=sys.stderr)

    # Identify gaps
    gaps = []
    for field in ["gitops", "terraform", "app_services", "project_id", "region", "cluster_name"]:
        if not config.get(field):
            gaps.append(field)

    return gaps


def display_config_noninteractive(config: Dict[str, Any]) -> None:
    """Display config summary for non-interactive mode (no prompts).

    Args:
        config: Configuration dict with all detected/user values.
    """
    print(_cyan("\n  Configuration (auto-detected + overrides):\n"), file=sys.stderr)
    fields = [
        ("GitOps", "gitops"),
        ("Terraform", "terraform"),
        ("App Services", "app_services"),
        ("Cloud", "cloud_provider"),
        ("Project ID", "project_id"),
        ("Region", "region"),
        ("Cluster", "cluster_name"),
        ("Project Name", "project_name"),
        ("Git Platform", "git_platform"),
        ("CI/CD", "ci_platform"),
    ]
    for label, key in fields:
        val = config.get(key, "")
        if key == "cloud_provider" and val:
            val = val.upper()
        display_val = val or "(not detected)"
        print(_gray(f"    {label + ':':16s} {display_val}"), file=sys.stderr)
    print("", file=sys.stderr)


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

def _prompt(message: str, default: str = "") -> str:
    """Prompt user for input with a default value.

    Args:
        message: Prompt message.
        default: Default value shown in brackets.

    Returns:
        User's input or default if empty.
    """
    if default:
        prompt_text = f"  {message} [{default}]: "
    else:
        prompt_text = f"  {message}: "

    try:
        answer = input(prompt_text).strip()
        return answer if answer else default
    except (EOFError, KeyboardInterrupt):
        print(_yellow("\n  Cancelled."), file=sys.stderr)
        sys.exit(0)


def prompt_gaps(config: Dict[str, Any], gaps: List[str]) -> Dict[str, Any]:
    """Prompt user for missing configuration values.

    Args:
        config: Current configuration dict (for defaults).
        gaps: List of field names that need values.

    Returns:
        Updated config dict with user-provided values.
    """
    if not gaps:
        return config

    print(_yellow(f"  {len(gaps)} item(s) need your input:\n"), file=sys.stderr)

    gap_labels = {
        "gitops": ("GitOps directory (Enter to skip)", ""),
        "terraform": ("Terraform directory (Enter to skip)", ""),
        "app_services": ("App Services directory (Enter to skip)", ""),
        "project_id": (
            "AWS Account ID (Enter to skip)"
            if config.get("cloud_provider") == "aws"
            else "Cloud Project ID (Enter to skip)",
            "",
        ),
        "region": (
            "Primary Region (Enter to skip)",
            "us-central1" if config.get("cloud_provider") == "gcp" else "us-east-1",
        ),
        "cluster_name": ("Cluster Name (Enter to skip)", ""),
    }

    for field in gaps:
        if field in gap_labels:
            label, default = gap_labels[field]
            answer = _prompt(label, default)
            if answer:
                config[field] = answer

    return config


def confirm_or_edit(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ask user to Accept / Edit / Cancel the configuration.

    Args:
        config: Current configuration dict.

    Returns:
        Final config dict, or None if cancelled.
    """
    print("  Proceed with this configuration?", file=sys.stderr)
    print("    1) Accept and install", file=sys.stderr)
    print("    2) Edit configuration", file=sys.stderr)
    print("    3) Cancel", file=sys.stderr)

    choice = _prompt("Choice", "1")

    if choice == "3":
        print(_yellow("\n  Installation cancelled.\n"), file=sys.stderr)
        return None

    if choice == "2":
        return edit_config(config)

    return config


def edit_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Let user edit all config fields interactively.

    Args:
        config: Current configuration dict.

    Returns:
        Updated config dict.
    """
    print(_gray("\n  Edit any field (press Enter to keep current value):\n"), file=sys.stderr)

    config["gitops"] = _prompt("GitOps directory", config.get("gitops", ""))
    config["terraform"] = _prompt("Terraform directory", config.get("terraform", ""))
    config["app_services"] = _prompt("App Services directory", config.get("app_services", ""))

    print("  Cloud provider:", file=sys.stderr)
    print("    1) GCP", file=sys.stderr)
    print("    2) AWS", file=sys.stderr)
    print("    3) Multi-cloud", file=sys.stderr)
    current_cloud = config.get("cloud_provider", "gcp")
    cloud_default = {"gcp": "1", "aws": "2", "multi-cloud": "3"}.get(current_cloud, "1")
    cloud_choice = _prompt("Choice", cloud_default)
    config["cloud_provider"] = {"1": "gcp", "2": "aws", "3": "multi-cloud"}.get(
        cloud_choice, current_cloud
    )

    config["project_id"] = _prompt("Project/Account ID", config.get("project_id", ""))
    config["region"] = _prompt("Primary region", config.get("region", ""))
    config["cluster_name"] = _prompt("Cluster name", config.get("cluster_name", ""))

    return config


# ---------------------------------------------------------------------------
# Summary display
# ---------------------------------------------------------------------------

def print_success(healthy: bool) -> None:
    """Print the success message and next steps.

    Args:
        healthy: Whether all verification checks passed.
    """
    print(_bold(_green("  ✓ Installation complete!\n")), file=sys.stderr)

    if not healthy:
        print(
            _yellow("  Some checks have warnings. Run `npx gaia-doctor` for details.\n"),
            file=sys.stderr,
        )

    print(_gray("  Next steps:"), file=sys.stderr)
    print(_gray("    1. Start Claude Code: claude"), file=sys.stderr)
    print(_gray("    2. Enrich context:    /speckit.init"), file=sys.stderr)
    print(_gray("    3. Verify health:     npx gaia-doctor\n"), file=sys.stderr)


def print_sync_summary(changes: Dict[str, Any]) -> None:
    """Print summary of what changed during a rescan+sync (Mode 2).

    Args:
        changes: Dict with keys: sections_updated, sections_preserved,
                 warnings, symlinks_refreshed, claude_md_synced, etc.
    """
    print(_bold(_cyan("\n  Rescan Summary\n")), file=sys.stderr)

    sections = changes.get("sections_updated", [])
    preserved = changes.get("sections_preserved", [])
    warnings = changes.get("warnings", [])

    if sections:
        print(f"  Sections updated: {', '.join(sections)}", file=sys.stderr)
    if preserved:
        print(f"  Sections preserved: {', '.join(preserved)}", file=sys.stderr)

    if changes.get("claude_md_synced"):
        print("  CLAUDE.md synced from template", file=sys.stderr)
    if changes.get("settings_merged"):
        print("  settings.json merged with template", file=sys.stderr)
    if changes.get("symlinks_refreshed"):
        print(f"  Symlinks refreshed: {changes['symlinks_refreshed']}", file=sys.stderr)
    if changes.get("hooks_installed"):
        print(f"  Git hooks installed: {changes['hooks_installed']} repo(s)", file=sys.stderr)

    if warnings:
        print(_yellow(f"\n  Warnings ({len(warnings)}):"), file=sys.stderr)
        for w in warnings[:10]:
            print(_yellow(f"    - {w}"), file=sys.stderr)
        if len(warnings) > 10:
            print(_gray(f"    ... and {len(warnings) - 10} more"), file=sys.stderr)

    print("", file=sys.stderr)
