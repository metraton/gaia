"""
gaia scan -- Sync workspace state into the Gaia DB.

Scan and install are separate flows. This command NEVER installs: it does not
create ``package.json``, run ``npm``, build ``.claude/``, or install git hooks.
Installation is owned by ``gaia install`` (bin/cli/install.py). All scan work
goes through one núcleo: ``tools.scan.core.scan_workspace``.

Entry points (explicit, single core):
  gaia scan                 -> scan the CURRENT workspace (must be inside one)
  gaia scan <path>          -> scan the named TARGET path
  gaia scan --workspace P   -> same as <path> (kept for back-compat)
  gaia scan --dry-run       -> report what would change without writing
  gaia scan --json          -> structured JSON summary
  gaia scan --scanners A,B  -> subset of scanners
  gaia scan --check-staleness -> exit 0 if context is fresh, else scan
  gaia scan --no-color / --verbose / -v

Outside a Gaia workspace AND with no target path, the command fails cleanly
("not in a Gaia workspace; enter one or pass a target") -- it does NOT fall
back to an install/bootstrap mode.

Exit codes:
  0  Success (or fresh-by-staleness)
  1  Error (scan failure, bad target, not in a workspace)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
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


def _resolve_target(args: argparse.Namespace) -> Optional[Path]:
    """Resolve the scan target path.

    Precedence: positional ``path`` arg, then ``--workspace`` flag, else
    ``None`` (meaning "scan the current workspace"). A returned path is
    resolved to absolute but NOT validated here.
    """
    explicit = getattr(args, "path", None) or getattr(args, "workspace", None)
    if explicit:
        return Path(explicit).resolve()
    return None


def _is_context_fresh(project_root: Path, staleness_hours: int) -> bool:
    """Return True if workspaces.last_scan_at is younger than staleness_hours.

    Reads from the DB workspace row. Returns False when the row is absent or
    last_scan_at is NULL.
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


def _emit_error(args: argparse.Namespace, msg: str) -> int:
    """Emit an error in the active output format and return exit code 1."""
    if getattr(args, "json", False):
        print(json.dumps({"status": "error", "error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Dry-run -- pure preview, never touches the DB
# ---------------------------------------------------------------------------

def _mode_dry_run(project_root: Path, args: argparse.Namespace) -> int:
    """Report what would change without writing. Does NOT touch the DB."""
    result: Dict[str, Any] = {
        "dry_run": True,
        "project_root": str(project_root),
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
        print(f"  would_scan    : {result['would_scan']}")
    return 0


# ---------------------------------------------------------------------------
# The scan run -- delegates to scan-core (tools.scan.core.scan_workspace)
# ---------------------------------------------------------------------------

def _run_scan(project_root: Path, scan_config, args: argparse.Namespace,
              scanner_version: str) -> int:
    """Scan ``project_root`` via scan-core and render results. No install.

    Single execution path for every non-dry-run entry point: scanning the
    current workspace, scanning a named target, and the npm-postinstall scan
    all land here.
    """
    from tools.scan.core import scan_workspace
    from tools.scan.ui import RailUI, collect_warnings, format_scanner_results

    from gaia.project import current as _project_current
    workspace = _project_current(cwd=project_root)

    ui = RailUI(version=scanner_version, color=_use_color(args))
    ui.start()
    ui.scanning()

    result = scan_workspace(project_root, workspace, config=scan_config)
    output = result.output

    display_sections = format_scanner_results(output, project_root=project_root)
    for sec in display_sections:
        ui.section(sec["name"], sec["lines"])

    warnings = collect_warnings(output)
    if warnings:
        ui.warning(len(warnings), warnings)

    duration_s = output.duration_ms / 1000
    ui.done(duration_s)
    ui.updated(len(output.sections_updated), len(output.sections_preserved))
    ui.footer("gaia.db updated")

    summary = _build_summary(output, scanner_version)
    summary["status"] = "error" if output.errors else "success"
    summary["marked_missing"] = result.marked_missing
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
        help="Sync workspace state into the Gaia DB (scan only -- never installs)",
        description=(
            "Scan a Gaia workspace and sync its state into gaia.db. With no "
            "target, scans the current workspace (you must be inside one). "
            "Pass a path to scan a named target. This command never installs."
        ),
    )
    p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Target path to scan (default: current workspace)",
    )
    p.add_argument(
        "--workspace",
        metavar="PATH",
        default=None,
        help="Target path to scan (back-compat alias for the positional path)",
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
    # Internal: used by `gaia install` Step 7 to run scan-core during npm
    # postinstall. It does NOT change what scan does (scan never installs); it
    # only relaxes the "must be inside a workspace" guard, because install has
    # just created the workspace and resolved its identity.
    p.add_argument(
        "--npm-postinstall",
        action="store_true",
        default=False,
        dest="npm_postinstall",
        help=argparse.SUPPRESS,
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
    target = _resolve_target(args)

    # Resolve the project root + enforce the explicit-entry-point contract.
    if target is not None:
        # On-demand: scan the named target.
        if not target.is_dir():
            return _emit_error(args, f"target not found: {target}")
        project_root = target
    else:
        # No target: scan the CURRENT workspace -- but only if we are inside
        # one. Outside a workspace with no target is a clean error, NOT an
        # install fallback. The npm-postinstall path is exempt: install has
        # just created the workspace and owns its identity.
        project_root = Path.cwd().resolve()
        if not getattr(args, "npm_postinstall", False):
            try:
                from tools.scan.core import is_gaia_workspace
                inside_workspace = is_gaia_workspace(project_root)
            except Exception:
                inside_workspace = False
            if not inside_workspace:
                return _emit_error(
                    args,
                    "not in a Gaia workspace -- enter one (cd into a directory "
                    "with a Gaia install) or pass a target path: "
                    "`gaia scan <path>`",
                )

    # Dry-run short-circuits before any tools.scan import / DB activity.
    if getattr(args, "dry_run", False):
        return _mode_dry_run(project_root, args)

    # Defer heavier imports until after the dry-run gate.
    try:
        from tools.scan.config import load_scan_config
        from tools.scan.scanners.tools import ToolScanner
    except Exception as exc:
        return _emit_error(args, f"failed to import tools.scan: {exc}")

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

    try:
        return _run_scan(project_root, scan_config, args, scanner_version)
    except Exception as exc:
        logging.exception("gaia scan failed")
        return _emit_error(args, str(exc))
