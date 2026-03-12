#!/usr/bin/env python3
"""
CLI entry point for gaia-scan — the SINGLE entry point for both fresh
projects and existing projects.

Modes:
  Mode 1 (Fresh):    No .claude/ directory — full setup flow.
  Mode 2 (Existing): .claude/ exists — rescan + sync + verify.
  Mode 3 (Non-interactive): --non-interactive flag — skips prompts.

Usage:
    gaia-scan                                        # Auto-detect mode
    gaia-scan --root /some/path                      # Scan a specific directory
    gaia-scan --scanners stack,git                   # Run subset of scanners
    gaia-scan --output /tmp/ctx.json                 # Custom output path
    gaia-scan --check-staleness                      # Exit 0 if fresh, else scan
    gaia-scan --verbose                              # Scanner-by-scanner progress
    gaia-scan --json                                 # JSON output to stdout (default)
    gaia-scan --non-interactive --cloud gcp          # CI/CD mode, no prompts

Exit codes:
    0  Success
    1  Error
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure the plugin root is on sys.path so `tools.scan` resolves correctly.
_SCRIPT_DIR = Path(__file__).resolve().parent
_PLUGIN_ROOT = _SCRIPT_DIR.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from tools.scan.config import ScanConfig, load_scan_config
from tools.scan.orchestrator import ScanOrchestrator
from tools.scan.registry import ScannerRegistry
from tools.scan.scanners.tools import ToolScanner


def _get_version():
    """Read version from package.json."""
    try:
        pkg_path = Path(__file__).resolve().parent.parent / "package.json"
        return json.load(open(pkg_path))["version"]
    except Exception:
        return "unknown"


scanner_version = _get_version()


def _is_context_fresh(project_root: Path, staleness_hours: int) -> bool:
    """Check if project-context.json exists and is within staleness threshold.

    Returns True if the context file exists and its last_scan timestamp is
    less than staleness_hours old.  Falls back to mtime if last_scan is not
    available.
    """
    context_path = project_root / ".claude" / "project-context" / "project-context.json"

    if not context_path.is_file():
        return False

    try:
        with open(context_path, "r") as f:
            data = json.load(f)

        last_scan = (
            data.get("metadata", {}).get("scan_config", {}).get("last_scan")
        )

        if last_scan:
            scan_dt = datetime.fromisoformat(last_scan)
            now = datetime.now(timezone.utc)
            age_hours = (now - scan_dt).total_seconds() / 3600
            return age_hours < staleness_hours

        # Fallback: use file mtime
        mtime = context_path.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        return age_hours < staleness_hours

    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _build_summary(output) -> dict:
    """Build a human-friendly summary dict from ScanOutput."""
    return {
        "scanner_version": scanner_version,
        "sections_updated": output.sections_updated,
        "sections_preserved": output.sections_preserved,
        "scanners_run": len(output.scanner_results),
        "warnings_count": len(output.warnings),
        "errors_count": len(output.errors),
        "duration_ms": round(output.duration_ms, 1),
        "warnings": output.warnings[:20],
        "errors": output.errors[:20],
    }


def _pretty_print(output, verbose: bool = False) -> None:
    """Print a human-readable summary to stderr (keeps stdout for JSON)."""
    n_scanners = len(output.scanner_results)
    n_sections = len(output.sections_updated)
    n_warnings = len(output.warnings)
    duration_s = output.duration_ms / 1000

    print(
        f"\ngaia scan complete  "
        f"({n_scanners} scanners, {n_sections} sections, "
        f"{n_warnings} warnings, {duration_s:.1f}s)",
        file=sys.stderr,
    )

    if verbose:
        print("\nScanner results:", file=sys.stderr)
        for name, result in sorted(output.scanner_results.items()):
            sections = ", ".join(result.sections.keys()) if result.sections else "(none)"
            warns = f"  [{len(result.warnings)} warn]" if result.warnings else ""
            print(
                f"  {name}: {sections}  ({result.duration_ms:.0f}ms){warns}",
                file=sys.stderr,
            )

    if output.warnings:
        print(f"\nWarnings ({n_warnings}):", file=sys.stderr)
        for w in output.warnings[:10]:
            print(f"  - {w}", file=sys.stderr)
        if n_warnings > 10:
            print(f"  ... and {n_warnings - 10} more", file=sys.stderr)

    if output.sections_preserved:
        print(
            f"\nPreserved agent-enriched sections: "
            f"{', '.join(output.sections_preserved)}",
            file=sys.stderr,
        )


def _extract_config_from_scan(output, project_root: Path, cli_args) -> dict:
    """Extract a config dict from scan output, CLI args, and env vars.

    Priority: CLI args > env vars > scan results > defaults.
    """
    ctx = output.context
    paths = ctx.get("paths", {})
    sections = ctx.get("sections", {})
    meta = ctx.get("metadata", {})

    infrastructure = sections.get("infrastructure", {})
    cloud_providers = infrastructure.get("cloud_providers", [])
    primary_cloud = cloud_providers[0] if isinstance(cloud_providers, list) and cloud_providers else {}
    ci_cd = infrastructure.get("ci_cd", [])
    primary_ci = ci_cd[0] if isinstance(ci_cd, list) and ci_cd else {}

    git_section = sections.get("git", {})
    project_identity = sections.get("project_identity", {})
    env_section = sections.get("environment", {})

    # Extract detected values
    detected_cloud = primary_cloud.get("name") or meta.get("cloud_provider") or "gcp"
    detected_project_id = (
        primary_cloud.get("project_id")
        or primary_cloud.get("account_id")
        or meta.get("project_id")
        or ""
    )
    detected_region = primary_cloud.get("region") or meta.get("primary_region") or ""

    # Git remotes for display
    remotes = git_section.get("remotes", [])
    git_remotes = []
    if isinstance(remotes, list):
        for r in remotes:
            if isinstance(r, dict):
                git_remotes.append({
                    "path": r.get("name", "."),
                    "remote": r.get("url", ""),
                })

    # Claude Code status
    env_tools = env_section.get("tools", [])
    claude_code = {"installed": False, "version": None}
    if isinstance(env_tools, list):
        claude_entry = next(
            (t for t in env_tools if isinstance(t, dict) and t.get("name") in ("claude", "claude-code")),
            None,
        )
        if claude_entry:
            claude_code = {"installed": True, "version": claude_entry.get("version")}

    # Build config with priority: CLI > env > scan > defaults
    config = {
        "gitops": (
            getattr(cli_args, "gitops", None)
            or os.environ.get("CLAUDE_GITOPS_DIR")
            or paths.get("gitops")
            or infrastructure.get("paths", {}).get("gitops")
            or ""
        ),
        "terraform": (
            getattr(cli_args, "terraform", None)
            or os.environ.get("CLAUDE_TERRAFORM_DIR")
            or paths.get("terraform")
            or infrastructure.get("paths", {}).get("terraform")
            or ""
        ),
        "app_services": (
            getattr(cli_args, "app_services", None)
            or os.environ.get("CLAUDE_APP_SERVICES_DIR")
            or paths.get("app_services")
            or infrastructure.get("paths", {}).get("app_services")
            or ""
        ),
        "cloud_provider": (
            getattr(cli_args, "cloud", None)
            or detected_cloud
        ),
        "project_id": (
            getattr(cli_args, "project_id", None)
            or os.environ.get("CLAUDE_PROJECT_ID")
            or detected_project_id
        ),
        "region": (
            getattr(cli_args, "region", None)
            or os.environ.get("CLAUDE_REGION")
            or detected_region
        ),
        "cluster_name": (
            getattr(cli_args, "cluster", None)
            or os.environ.get("CLAUDE_CLUSTER_NAME")
            or ""
        ),
        "project_name": (
            project_identity.get("name")
            or meta.get("project_name")
            or project_root.name
        ),
        "git_platform": git_section.get("platform"),
        "ci_platform": primary_ci.get("platform") if isinstance(primary_ci, dict) else None,
        "claude_code": claude_code,
        "git_remotes": git_remotes,
    }

    return config


def _run_scan(project_root: Path, scan_config: ScanConfig) -> object:
    """Run the 6 Python scanners and return ScanOutput."""
    registry = ScannerRegistry()
    orchestrator = ScanOrchestrator(registry=registry, config=scan_config)
    return orchestrator.run(project_root=project_root)


# ============================================================================
# Mode 1: Fresh project setup (no .claude/ directory)
# ============================================================================

def _mode_fresh(project_root: Path, scan_config: ScanConfig, args) -> int:
    """Full setup flow for a fresh project.

    Steps: SCAN -> DISPLAY -> GAPS -> CONFIRM -> INSTALL -> VERIFY -> NEXT
    """
    from tools.scan.setup import (
        copy_claude_md,
        copy_settings_json,
        create_claude_directory,
        ensure_claude_code,
        ensure_gaia_ops_package,
        generate_governance,
        generate_project_context,
        install_git_hooks,
    )
    from tools.scan.ui import (
        confirm_or_edit,
        display_config,
        display_config_noninteractive,
        print_success,
        prompt_gaps,
    )
    from tools.scan.verify import print_verification, run_verification

    # Step 1: SCAN
    output = _run_scan(project_root, scan_config)
    _pretty_print(output, verbose=args.verbose)

    # Extract config from scan results
    config = _extract_config_from_scan(output, project_root, args)

    non_interactive = getattr(args, "non_interactive", False)

    if non_interactive:
        # Mode 3: Non-interactive
        display_config_noninteractive(config)
    else:
        # Step 2: DISPLAY
        gaps = display_config(config, project_root)

        # Step 3: GAPS
        config = prompt_gaps(config, gaps)

        # Step 4: CONFIRM
        config = confirm_or_edit(config)
        if config is None:
            return 0  # User cancelled

    # Step 5: INSTALL
    print("\n  Installing...\n", file=sys.stderr)

    # 5.1 Claude Code
    skip_claude = getattr(args, "skip_claude_install", False)
    claude_status = ensure_claude_code(skip_install=skip_claude)
    if claude_status.get("installed"):
        print("  ✓ Claude Code ready", file=sys.stderr)
    else:
        print("  ⚠ Claude Code not installed", file=sys.stderr)

    # 5.2 npm package
    if ensure_gaia_ops_package(project_root):
        print("  ✓ @jaguilar87/gaia-ops installed", file=sys.stderr)
    else:
        print("  ⚠ Package install failed (continuing with local files)", file=sys.stderr)

    # 5.3 .claude/ directory with symlinks
    created = create_claude_directory(project_root)
    print(f"  ✓ .claude/ directory created ({len(created)} symlinks)", file=sys.stderr)

    # 5.4 CLAUDE.md
    if copy_claude_md(project_root):
        print("  ✓ CLAUDE.md synced", file=sys.stderr)

    # 5.5 settings.json
    if copy_settings_json(project_root):
        print("  ✓ settings.json ready", file=sys.stderr)

    # 5.6 Git hooks
    hooks_count = install_git_hooks(project_root)
    if hooks_count > 0:
        print(f"  ✓ Git hooks installed ({hooks_count} repo(s))", file=sys.stderr)

    # 5.7 project-context.json
    if generate_project_context(project_root, config, scan_context=output.context):
        print("  ✓ project-context.json generated", file=sys.stderr)

    # 5.8 governance.md
    if generate_governance(project_root, config):
        print("  ✓ governance.md ready", file=sys.stderr)

    # Step 6: VERIFY
    results = run_verification(project_root)
    healthy = print_verification(results)

    # Step 7: NEXT
    print_success(healthy)

    # Also print JSON summary to stdout
    summary = _build_summary(output)
    summary["status"] = "success"
    summary["mode"] = "fresh"
    print(json.dumps(summary, indent=2), file=sys.stdout)

    return 0


# ============================================================================
# Mode 2: Existing project rescan + sync
# ============================================================================

def _mode_existing(project_root: Path, scan_config: ScanConfig, args) -> int:
    """Rescan + sync flow for an existing project.

    Steps: SCAN -> MERGE -> SYNC -> VERIFY -> SUMMARY
    """
    from tools.scan.setup import (
        copy_claude_md,
        copy_settings_json,
        create_claude_directory,
        install_git_hooks,
    )
    from tools.scan.ui import print_sync_summary
    from tools.scan.verify import print_verification, run_verification

    changes: dict = {}

    # Step 1: SCAN (orchestrator handles merge into project-context.json)
    output = _run_scan(project_root, scan_config)
    _pretty_print(output, verbose=args.verbose)

    changes["sections_updated"] = output.sections_updated
    changes["sections_preserved"] = output.sections_preserved
    changes["warnings"] = output.warnings

    # Step 3: SYNC — refresh CLAUDE.md, merge settings.json, refresh symlinks
    if copy_claude_md(project_root):
        changes["claude_md_synced"] = True

    if copy_settings_json(project_root):
        changes["settings_merged"] = True

    created = create_claude_directory(project_root)
    if created:
        changes["symlinks_refreshed"] = len(created)

    hooks_count = install_git_hooks(project_root)
    if hooks_count > 0:
        changes["hooks_installed"] = hooks_count

    # Step 4: VERIFY
    results = run_verification(project_root)
    healthy = print_verification(results)

    # Step 5: SUMMARY
    print_sync_summary(changes)

    # JSON summary to stdout
    summary = _build_summary(output)
    summary["status"] = "error" if output.errors else "success"
    summary["mode"] = "existing"
    print(json.dumps(summary, indent=2), file=sys.stdout)

    return 1 if output.errors else 0


# ============================================================================
# Legacy scan-only mode
# ============================================================================

def _mode_scan_only(project_root: Path, scan_config: ScanConfig, args) -> int:
    """Scan-only mode: run scanners, write context, print JSON.

    This mode runs scanners only, writes context, and prints JSON.
    Useful for CI/CD or when only scan data is needed.
    """
    output = _run_scan(project_root, scan_config)
    _pretty_print(output, verbose=args.verbose)

    summary = _build_summary(output)
    summary["status"] = "error" if output.errors else "success"
    print(json.dumps(summary, indent=2), file=sys.stdout)

    return 1 if output.errors else 0


# ============================================================================
# CLI
# ============================================================================

def main(argv: list = None) -> int:
    """CLI main entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        prog="gaia-scan",
        description=(
            "Scan a project and generate/update project-context.json. "
            "For fresh projects (no .claude/), runs the full setup flow. "
            "For existing projects, rescans and syncs configuration."
        ),
    )

    # Existing scan flags (backward compatible)
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help="Project root directory (default: current working directory)",
    )
    parser.add_argument(
        "--scanners",
        type=str,
        default=None,
        help="Comma-separated list of scanners to run (default: all)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Custom output path for project-context.json",
    )
    parser.add_argument(
        "--check-staleness",
        action="store_true",
        default=False,
        help="Only scan if context is stale or missing; exit 0 immediately if fresh",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Print scanner-by-scanner progress",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="JSON-only output to stdout (scan-only mode, no setup)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"gaia-scan {_get_version()}",
    )

    # New setup flags (from gaia-init)
    parser.add_argument(
        "--non-interactive", "-y",
        action="store_true",
        default=False,
        help="Accept detected values without confirmation (CI/CD mode)",
    )
    parser.add_argument(
        "--cloud",
        type=str,
        default=None,
        help="Override cloud provider (gcp, aws, multi-cloud)",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help="Override primary region",
    )
    parser.add_argument(
        "--cluster",
        type=str,
        default=None,
        help="Override cluster name",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        dest="project_id",
        help="Override cloud project/account ID",
    )
    parser.add_argument(
        "--gitops",
        type=str,
        default=None,
        help="Override GitOps directory path",
    )
    parser.add_argument(
        "--terraform",
        type=str,
        default=None,
        help="Override Terraform directory path",
    )
    parser.add_argument(
        "--app-services",
        type=str,
        default=None,
        dest="app_services",
        help="Override App Services directory path",
    )
    parser.add_argument(
        "--skip-claude-install",
        action="store_true",
        default=False,
        dest="skip_claude_install",
        help="Skip Claude Code CLI installation",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        default=False,
        dest="scan_only",
        help="Run scanners only, do not perform setup or sync",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="Scan all tools including extended (low-value) ones",
    )

    args = parser.parse_args(argv)

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [gaia-scan] %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    try:
        project_root = Path(args.root).resolve() if args.root else Path.cwd()

        if not project_root.is_dir():
            print(
                json.dumps({"error": f"Project root not found: {project_root}"}),
                file=sys.stdout,
            )
            return 1

        # Set extended tool scanning flag before registry discovers scanners
        if args.full:
            ToolScanner.scan_extended = True

        # Load scan config from existing project-context.json
        scan_config = load_scan_config(project_root)
        scan_config.project_root = project_root
        scan_config.verbose = args.verbose

        if args.scanners:
            scan_config.scanners = [
                s.strip() for s in args.scanners.split(",") if s.strip()
            ]

        if args.output:
            scan_config.output_path = Path(args.output).resolve()

        # Staleness check (short-circuit)
        if args.check_staleness:
            if _is_context_fresh(project_root, scan_config.staleness_hours):
                result = {
                    "status": "fresh",
                    "message": "Context is up to date, scan skipped.",
                }
                print(json.dumps(result), file=sys.stdout)
                return 0

        # Mode selection
        # --json or --scan-only: backward-compatible scan-only mode
        if args.json or args.scan_only:
            return _mode_scan_only(project_root, scan_config, args)

        # Detect mode based on .claude/ existence
        claude_dir = project_root / ".claude"
        if claude_dir.is_dir():
            # Mode 2: Existing project
            return _mode_existing(project_root, scan_config, args)
        else:
            # Mode 1: Fresh project
            return _mode_fresh(project_root, scan_config, args)

    except Exception as exc:
        error_result = {"status": "error", "error": str(exc)}
        print(json.dumps(error_result), file=sys.stdout)
        logging.exception("gaia-scan failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
