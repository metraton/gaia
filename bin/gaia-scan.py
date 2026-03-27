#!/usr/bin/env python3
"""
CLI entry point for gaia-scan -- the SINGLE entry point for both fresh
projects and existing projects.

Modes:
  Mode 1 (Fresh):    No .claude/ directory -- full setup flow (automatic).
  Mode 2 (Existing): .claude/ exists -- rescan + sync + verify.
  Mode 3 (Scan-only): --scan-only or --json -- scanners only, JSON output.

Usage:
    gaia-scan                                        # Auto-detect mode
    gaia-scan --root /some/path                      # Scan a specific directory
    gaia-scan --scanners stack,git                   # Run subset of scanners
    gaia-scan --output /tmp/ctx.json                 # Custom output path
    gaia-scan --check-staleness                      # Exit 0 if fresh, else scan
    gaia-scan --verbose                              # Scanner-by-scanner progress
    gaia-scan --json                                 # JSON output to stdout
    gaia-scan --scan-only                            # Scan only, no setup/sync

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
        with open(pkg_path) as f:
            return json.load(f)["version"]
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


def _run_scan(project_root: Path, scan_config: ScanConfig) -> object:
    """Run the 6 Python scanners and return ScanOutput."""
    registry = ScannerRegistry()
    orchestrator = ScanOrchestrator(registry=registry, config=scan_config)
    return orchestrator.run(project_root=project_root)


def _extract_config_from_scan(output, project_root: Path) -> dict:
    """Extract a config dict from scan output for setup functions.

    Priority: env vars > scan results > defaults.
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

    # Extract detected values
    detected_cloud = primary_cloud.get("name") or meta.get("cloud_provider") or "gcp"
    detected_project_id = (
        primary_cloud.get("project_id")
        or primary_cloud.get("account_id")
        or meta.get("project_id")
        or ""
    )
    detected_region = primary_cloud.get("region") or meta.get("primary_region") or ""

    config = {
        "gitops": (
            os.environ.get("CLAUDE_GITOPS_DIR")
            or paths.get("gitops")
            or infrastructure.get("paths", {}).get("gitops")
            or ""
        ),
        "terraform": (
            os.environ.get("CLAUDE_TERRAFORM_DIR")
            or paths.get("terraform")
            or infrastructure.get("paths", {}).get("terraform")
            or ""
        ),
        "app_services": (
            os.environ.get("CLAUDE_APP_SERVICES_DIR")
            or paths.get("app_services")
            or infrastructure.get("paths", {}).get("app_services")
            or ""
        ),
        "cloud_provider": detected_cloud,
        "project_id": (
            os.environ.get("CLAUDE_PROJECT_ID")
            or detected_project_id
        ),
        "region": (
            os.environ.get("CLAUDE_REGION")
            or detected_region
        ),
        "cluster_name": (
            os.environ.get("CLAUDE_CLUSTER_NAME")
            or ""
        ),
        "project_name": (
            project_identity.get("name")
            or meta.get("project_name")
            or project_root.name
        ),
        "git_platform": git_section.get("platform"),
        "ci_platform": primary_ci.get("platform") if isinstance(primary_ci, dict) else None,
    }

    return config


# ============================================================================
# Mode 1: Fresh project setup (no .claude/ directory)
# ============================================================================

def _mode_fresh(project_root: Path, scan_config: ScanConfig, args) -> int:
    """Full setup flow for a fresh project. Fully automatic, no prompts.

    Steps: SCAN -> DISPLAY -> INSTALL -> VERIFY -> SUMMARY
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
        merge_hooks_to_settings_local,
    )
    from tools.scan.ui import (
        RailUI,
        collect_created_summary,
        collect_warnings,
        format_scanner_results,
    )
    from tools.scan.verify import run_verification

    ui = RailUI(version=scanner_version, color=_use_color(args))

    # Step 1: Header + scanning
    ui.start()
    ui.scanning()

    # Step 2: SCAN
    output = _run_scan(project_root, scan_config)

    # Step 3: DISPLAY results
    display_sections = format_scanner_results(output, project_root=project_root)
    for sec in display_sections:
        ui.section(sec["name"], sec["lines"])

    # Warnings
    warnings = collect_warnings(output)
    if warnings:
        ui.warning(len(warnings), warnings)

    # Extract config from scan results
    config = _extract_config_from_scan(output, project_root)

    # Step 4: INSTALL (automatic, no prompts)
    skip_claude = getattr(args, "skip_claude_install", False)
    npm_postinstall = getattr(args, "npm_postinstall", False)
    ensure_claude_code(skip_install=skip_claude)
    if not npm_postinstall:
        # Skip when called from npm postinstall to avoid re-entrance
        ensure_gaia_ops_package(project_root)
    create_claude_directory(project_root)
    copy_claude_md(project_root)
    copy_settings_json(project_root)
    merge_hooks_to_settings_local(project_root)
    install_git_hooks(project_root)
    generate_project_context(project_root, config, scan_context=output.context)
    generate_governance(project_root, config)

    # Step 5: VERIFY (silent -- used for health check)
    run_verification(project_root)

    # Step 6: Done + Created summary
    duration_s = output.duration_ms / 1000
    ui.done(duration_s)

    created_items = collect_created_summary(project_root, output)
    if created_items:
        ui.created(created_items)

    ui.footer("Run claude to start. Context will enrich automatically.")

    # JSON summary to stdout (only when --json or piped)
    summary = _build_summary(output)
    summary["status"] = "success"
    summary["mode"] = "fresh"
    if _should_print_json(args):
        print(json.dumps(summary, indent=2), file=sys.stdout)

    return 0


# ============================================================================
# Mode 2: Existing project rescan + sync
# ============================================================================

def _mode_existing(project_root: Path, scan_config: ScanConfig, args) -> int:
    """Rescan + sync flow for an existing project. Fully automatic.

    Steps: SCAN -> DISPLAY -> SYNC -> SUMMARY
    """
    from tools.scan.setup import (
        copy_claude_md,
        copy_settings_json,
        create_claude_directory,
        install_git_hooks,
        merge_hooks_to_settings_local,
    )
    from tools.scan.ui import (
        RailUI,
        collect_warnings,
        format_scanner_results,
    )
    from tools.scan.verify import run_verification

    ui = RailUI(version=scanner_version, color=_use_color(args))

    # Step 1: Header + scanning
    ui.start()
    ui.scanning()

    # Step 2: SCAN
    output = _run_scan(project_root, scan_config)

    # Step 3: DISPLAY results
    display_sections = format_scanner_results(output, project_root=project_root)
    for sec in display_sections:
        ui.section(sec["name"], sec["lines"])

    # Warnings
    warnings = collect_warnings(output)
    if warnings:
        ui.warning(len(warnings), warnings)

    # Step 4: SYNC
    copy_claude_md(project_root)
    copy_settings_json(project_root)
    merge_hooks_to_settings_local(project_root)
    create_claude_directory(project_root)
    install_git_hooks(project_root)

    # Step 5: VERIFY (silent)
    run_verification(project_root)

    # Step 6: Done + Updated summary
    duration_s = output.duration_ms / 1000
    ui.done(duration_s)

    sections_updated = len(output.sections_updated)
    sections_preserved = len(output.sections_preserved)
    ui.updated(sections_updated, sections_preserved)

    ui.footer("Ready.")

    # JSON summary to stdout (only when --json or piped)
    summary = _build_summary(output)
    summary["status"] = "error" if output.errors else "success"
    summary["mode"] = "existing"
    if _should_print_json(args):
        print(json.dumps(summary, indent=2), file=sys.stdout)

    return 1 if output.errors else 0


# ============================================================================
# Mode 3: Scan-only mode
# ============================================================================

def _mode_scan_only(project_root: Path, scan_config: ScanConfig, args) -> int:
    """Scan-only mode: run scanners, write context, print JSON.

    Compact rail output showing section names on one line.
    """
    from tools.scan.ui import RailUI, format_scanner_results

    ui = RailUI(version=scanner_version, color=_use_color(args))

    # Header (no scanning indicator for compact mode)
    ui.start()

    # Scan
    output = _run_scan(project_root, scan_config)

    # Compact section list
    display_sections = format_scanner_results(output, project_root=project_root)
    section_names = [sec["name"] for sec in display_sections]
    if section_names:
        ui.section_compact(section_names)

    # Done
    duration_s = output.duration_ms / 1000
    sections_count = len(output.sections_updated)
    ui.done(duration_s, suffix=f"{sections_count} sections updated")

    ui.footer("project-context.json updated")

    # JSON summary to stdout (only when --json or piped)
    summary = _build_summary(output)
    summary["status"] = "error" if output.errors else "success"
    if _should_print_json(args):
        print(json.dumps(summary, indent=2), file=sys.stdout)

    return 1 if output.errors else 0


# ============================================================================
# Helpers
# ============================================================================

def _should_print_json(args) -> bool:
    """Determine if JSON summary should be printed to stdout.

    Print JSON only when --json flag is explicitly passed.
    Never auto-print based on TTY detection -- this causes spurious stdout
    output when invoked by tools (e.g., Claude Code) that capture stdout.
    """
    return getattr(args, "json", False)


def _use_color(args) -> bool:
    """Determine if color output should be used."""
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return True


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

    # Core flags
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
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )

    # Backward compat flags (no-ops, behavior is now always non-interactive)
    parser.add_argument(
        "--non-interactive", "-y",
        action="store_true",
        default=False,
        help="(no-op, kept for backward compatibility) Accept detected values without confirmation",
    )
    parser.add_argument(
        "--skip-claude-install",
        action="store_true",
        default=False,
        dest="skip_claude_install",
        help="Skip Claude Code CLI installation",
    )
    parser.add_argument(
        "--npm-postinstall",
        action="store_true",
        default=False,
        dest="npm_postinstall",
        help="Called from npm postinstall: skip Claude Code install and npm package install to avoid re-entrance",
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

        # --npm-postinstall implies --skip-claude-install and skips ensure_gaia_ops_package
        if args.npm_postinstall:
            args.skip_claude_install = True

        # Mode selection
        # --json or --scan-only: scan-only mode
        if args.json or args.scan_only:
            return _mode_scan_only(project_root, scan_config, args)

        # --npm-postinstall: fresh install mode with re-entrance protection
        if args.npm_postinstall:
            return _mode_fresh(project_root, scan_config, args)

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
