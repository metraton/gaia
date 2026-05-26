"""
gaia scan -- Sync workspace state into Gaia DB.

Imports `tools.scan` directly instead of spawning a subprocess, so callers
(CLI, hooks, tests) share a single process and a single import path.

Modes:
  gaia scan                       -> existing workspace: rescan + sync
  gaia scan --fresh               -> fresh workspace: bootstrap .claude/, hooks, settings
  gaia scan --workspace PATH      -> point at a specific project root
  gaia scan --dry-run             -> report what would change without writing
  gaia scan --json                -> structured JSON output (scan-only, no setup/sync)
  gaia scan --scanners A,B,C      -> subset of scanners
  gaia scan --check-staleness     -> exit 0 if context is fresh, else scan
  gaia scan --no-color            -> disable ANSI color
  gaia scan --verbose / -v        -> per-scanner progress

Exit codes:
  0  Success (or fresh-by-staleness)
  1  Error (scan failure, bad workspace, etc.)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# bin/cli/scan.py -> bin/cli/ -> bin/ -> repo root
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_version() -> str:
    """Read version from package.json."""
    try:
        pkg_path = _PLUGIN_ROOT / "package.json"
        with open(pkg_path) as f:
            return json.load(f)["version"]
    except Exception:
        return "unknown"


def _resolve_workspace(workspace: Optional[str]) -> Path:
    """Resolve the workspace path: explicit --workspace wins, else cwd."""
    if workspace:
        return Path(workspace).resolve()
    return Path.cwd().resolve()


def _is_context_fresh(project_root: Path, staleness_hours: int) -> bool:
    """Return True if last_scan_at in DB is younger than staleness_hours.

    T1.3: reads from workspaces.last_scan_at instead of project-context.json.
    Returns False when the workspace row is absent or last_scan_at is NULL.
    """
    try:
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=project_root)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT last_scan_at FROM workspaces WHERE name = ?", (ws,)
            ).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            return False
        scan_dt = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_hours = (now - scan_dt).total_seconds() / 3600
        return age_hours < staleness_hours
    except Exception:
        return False


def _build_summary(output, scanner_version: str) -> Dict[str, Any]:
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


def _use_color(args: argparse.Namespace) -> bool:
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return True


# ---------------------------------------------------------------------------
# Mode implementations -- in-process imports of tools.scan
# ---------------------------------------------------------------------------

def _run_scan(project_root: Path, scan_config) -> Any:
    """Run scanners and return ScanOutput. Used by all non-dry-run modes."""
    from tools.scan.orchestrator import ScanOrchestrator
    from tools.scan.registry import ScannerRegistry

    registry = ScannerRegistry()
    orchestrator = ScanOrchestrator(registry=registry, config=scan_config)
    return orchestrator.run(project_root=project_root)


def _mode_dry_run(project_root: Path, args: argparse.Namespace) -> int:
    """Report what would change without writing.

    Does NOT touch the SQLite DB -- pure preview.
    T1.3: reads last_scan_at from workspaces DB row instead of project-context.json.
    """
    last_scan = None
    try:
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=project_root)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT last_scan_at FROM workspaces WHERE name = ?", (ws,)
            ).fetchone()
            if row:
                last_scan = row[0]
        finally:
            con.close()
    except Exception:
        pass

    result: Dict[str, Any] = {
        "dry_run": True,
        "project_root": str(project_root),
        "fresh": getattr(args, "fresh", False),
        "last_scan": last_scan or "unknown",
        "would_scan": (
            "all scanners (stack, git, infrastructure, environment, "
            "orchestration, architecture)"
        ),
    }

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        print("[dry-run] gaia scan would execute:")
        print(f"  project_root  : {result['project_root']}")
        print(f"  fresh         : {result['fresh']}")
        if result.get("last_scan") and result["last_scan"] != "unknown":
            print(f"  last_scan     : {result['last_scan']}")
        print(f"  would_scan    : {result['would_scan']}")
    return 0


def _mode_fresh(project_root: Path, scan_config, args: argparse.Namespace,
                scanner_version: str) -> int:
    """Bootstrap a fresh workspace: scan + create .claude/, hooks, settings."""
    from tools.scan.setup import (
        copy_claude_md,
        copy_settings_json,
        create_claude_directory,
        ensure_claude_code,
        ensure_gaia_ops_package,
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
    ui.start()
    ui.scanning()

    output = _run_scan(project_root, scan_config)

    display_sections = format_scanner_results(output, project_root=project_root)
    for sec in display_sections:
        ui.section(sec["name"], sec["lines"])

    warnings = collect_warnings(output)
    if warnings:
        ui.warning(len(warnings), warnings)

    skip_claude = getattr(args, "skip_claude_install", False)
    npm_postinstall = getattr(args, "npm_postinstall", False)
    ensure_claude_code(skip_install=skip_claude)
    if not npm_postinstall:
        ensure_gaia_ops_package(project_root)
    create_claude_directory(project_root)
    copy_claude_md(project_root)
    copy_settings_json(project_root)
    merge_hooks_to_settings_local(project_root)
    install_git_hooks(project_root)

    run_verification(project_root)

    duration_s = output.duration_ms / 1000
    ui.done(duration_s)

    created_items = collect_created_summary(project_root, output)
    if created_items:
        ui.created(created_items)

    ui.footer("Run claude to start. Context will enrich automatically.")

    # Record scan timestamp in DB (T1.2: D8).
    try:
        from gaia.project import current as _project_current
        from gaia.store.writer import set_workspace_last_scan_at
        _ws = _project_current(cwd=project_root)
        set_workspace_last_scan_at(_ws)
    except Exception:  # pragma: no cover -- non-fatal
        pass

    summary = _build_summary(output, scanner_version)
    summary["status"] = "success"
    summary["mode"] = "fresh"
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
    return 0


def _mode_existing(project_root: Path, scan_config, args: argparse.Namespace,
                   scanner_version: str) -> int:
    """Re-sync an existing workspace: scan + refresh .claude/ contents."""
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
    ui.start()
    ui.scanning()

    output = _run_scan(project_root, scan_config)

    display_sections = format_scanner_results(output, project_root=project_root)
    for sec in display_sections:
        ui.section(sec["name"], sec["lines"])

    warnings = collect_warnings(output)
    if warnings:
        ui.warning(len(warnings), warnings)

    copy_claude_md(project_root)
    copy_settings_json(project_root)
    merge_hooks_to_settings_local(project_root)
    create_claude_directory(project_root)
    install_git_hooks(project_root)

    run_verification(project_root)

    duration_s = output.duration_ms / 1000
    ui.done(duration_s)

    sections_updated = len(output.sections_updated)
    sections_preserved = len(output.sections_preserved)
    ui.updated(sections_updated, sections_preserved)
    ui.footer("Ready.")

    # Record scan timestamp in DB (T1.2: D8).
    if not output.errors:
        try:
            from gaia.project import current as _project_current
            from gaia.store.writer import set_workspace_last_scan_at
            _ws = _project_current(cwd=project_root)
            set_workspace_last_scan_at(_ws)
        except Exception:  # pragma: no cover -- non-fatal
            pass

    summary = _build_summary(output, scanner_version)
    summary["status"] = "error" if output.errors else "success"
    summary["mode"] = "existing"
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
    return 1 if output.errors else 0


def _mode_scan_only(project_root: Path, scan_config, args: argparse.Namespace,
                    scanner_version: str) -> int:
    """Scan-only: run scanners, write context, emit JSON summary. No setup."""
    from tools.scan.ui import RailUI, format_scanner_results

    ui = RailUI(version=scanner_version, color=_use_color(args))
    ui.start()

    output = _run_scan(project_root, scan_config)

    display_sections = format_scanner_results(output, project_root=project_root)
    section_names = [sec["name"] for sec in display_sections]
    if section_names:
        ui.section_compact(section_names)

    duration_s = output.duration_ms / 1000
    sections_count = len(output.sections_updated)
    ui.done(duration_s, suffix=f"{sections_count} sections updated")
    ui.footer("gaia.db updated")

    # Record scan timestamp in DB (T1.2: D8).
    if not output.errors:
        try:
            from gaia.project import current as _project_current
            from gaia.store.writer import set_workspace_last_scan_at
            _ws = _project_current(cwd=project_root)
            set_workspace_last_scan_at(_ws)
        except Exception:  # pragma: no cover -- non-fatal
            pass

    summary = _build_summary(output, scanner_version)
    summary["status"] = "error" if output.errors else "success"
    summary["mode"] = "scan_only"
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
    return 1 if output.errors else 0


# ---------------------------------------------------------------------------
# Plugin registration (discovered by bin/gaia)
# ---------------------------------------------------------------------------

def register(subparsers) -> argparse.ArgumentParser:
    """Register the `scan` subcommand with the root parser."""
    p = subparsers.add_parser(
        "scan",
        help="Sync workspace state into the Gaia DB",
        description=(
            "Scan the current workspace and sync state into Gaia DB. "
            "With --fresh, bootstraps .claude/, hooks, and settings for a new "
            "workspace. Without flags, re-syncs an existing workspace."
        ),
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        default=False,
        help="Bootstrap a fresh workspace (.claude/, hooks, settings)",
    )
    p.add_argument(
        "--workspace",
        metavar="PATH",
        default=None,
        help="Workspace root path (default: current working directory)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Report what would change without writing",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit structured JSON summary on stdout",
    )
    p.add_argument(
        "--scanners",
        metavar="A,B,C",
        default=None,
        help="Comma-separated subset of scanners to run (default: all)",
    )
    p.add_argument(
        "--check-staleness",
        action="store_true",
        default=False,
        dest="check_staleness",
        help="Exit 0 if context is fresh, else scan",
    )
    p.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="Scan all tools including extended (low-value) ones",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Print scanner-by-scanner progress",
    )
    # Backward-compat / fresh-mode helpers
    p.add_argument(
        "--skip-claude-install",
        action="store_true",
        default=False,
        dest="skip_claude_install",
        help="Skip Claude Code CLI installation during --fresh",
    )
    p.add_argument(
        "--npm-postinstall",
        action="store_true",
        default=False,
        dest="npm_postinstall",
        help="Called from npm postinstall: skip Claude install + npm bootstrap",
    )
    return p


def cmd_scan(args: argparse.Namespace) -> int:
    """Dispatch handler for `gaia scan`."""
    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [gaia scan] %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    scanner_version = _get_version()
    project_root = _resolve_workspace(getattr(args, "workspace", None))

    if not project_root.is_dir():
        msg = f"workspace not found: {project_root}"
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    # Dry-run short-circuits before any tools.scan import / DB activity.
    if getattr(args, "dry_run", False):
        return _mode_dry_run(project_root, args)

    # Defer heavier imports until after the dry-run gate.
    try:
        from tools.scan.config import load_scan_config
        from tools.scan.scanners.tools import ToolScanner
    except Exception as exc:
        msg = f"failed to import tools.scan: {exc}"
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    if getattr(args, "full", False):
        ToolScanner.scan_extended = True

    scan_config = load_scan_config(project_root)
    scan_config.project_root = project_root
    scan_config.verbose = getattr(args, "verbose", False)

    if getattr(args, "scanners", None):
        scan_config.scanners = [
            s.strip() for s in args.scanners.split(",") if s.strip()
        ]

    if getattr(args, "check_staleness", False):
        if _is_context_fresh(project_root, scan_config.staleness_hours):
            result = {
                "status": "fresh",
                "message": "Context is up to date, scan skipped.",
            }
            if getattr(args, "json", False):
                print(json.dumps(result))
            else:
                print(result["message"])
            return 0

    # --npm-postinstall implies --skip-claude-install and forces fresh mode.
    if getattr(args, "npm_postinstall", False):
        args.skip_claude_install = True
        try:
            return _mode_fresh(project_root, scan_config, args, scanner_version)
        except Exception as exc:
            msg = str(exc)
            if getattr(args, "json", False):
                print(json.dumps({"status": "error", "error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            logging.exception("gaia scan failed")
            return 1

    # --json without --fresh: scan-only mode (no setup, no sync).
    if getattr(args, "json", False) and not getattr(args, "fresh", False):
        try:
            return _mode_scan_only(project_root, scan_config, args, scanner_version)
        except Exception as exc:
            print(json.dumps({"status": "error", "error": str(exc)}))
            logging.exception("gaia scan failed")
            return 1

    try:
        if getattr(args, "fresh", False):
            return _mode_fresh(project_root, scan_config, args, scanner_version)
        # Detect mode based on .claude/ presence
        claude_dir = project_root / ".claude"
        if claude_dir.is_dir():
            return _mode_existing(project_root, scan_config, args, scanner_version)
        # No --fresh and no .claude/: implicitly fresh
        return _mode_fresh(project_root, scan_config, args, scanner_version)
    except Exception as exc:
        msg = str(exc)
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        logging.exception("gaia scan failed")
        return 1
