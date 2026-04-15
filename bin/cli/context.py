"""
gaia context -- Display and refresh project context.

Subcommands:
  gaia context show [--section SECTION] [--json]   Display project-context.json
  gaia context scan [--dry-run] [--json]            Run project scanner
"""

import json
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Root detection
# ---------------------------------------------------------------------------

def _find_project_root(start: Path) -> Path | None:
    """Locate the project root that owns .claude/project-context/project-context.json.

    Resolution order:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code at runtime) -- its
       parent is the project root.
    2. Walk up from ``start`` looking for .claude/project-context/project-context.json
       (has actual user context data, not just plugin config).
    3. Walk up from ``start`` looking for .claude/project-context/ directory.
    4. Walk up from ``start`` for any .claude/ directory (original fallback).

    This ensures the CLI skips a plugin's own .claude/ config dir (e.g.,
    gaia-ops-dev/.claude/) and continues up to the user's project root when
    the CLI is invoked from inside a plugin subdirectory.
    """
    import os
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        candidate = Path(plugin_data)
        if candidate.is_dir():
            return candidate.parent
        return candidate.parent

    current = start.resolve()
    candidates = [current, *current.parents]

    # Pass 1: prefer a root that has the actual project-context.json file.
    for parent in candidates:
        if (parent / ".claude" / "project-context" / "project-context.json").is_file():
            return parent

    # Pass 2: accept any root that has project-context/ directory.
    for parent in candidates:
        if (parent / ".claude" / "project-context").is_dir():
            return parent

    # Pass 3: original fallback -- any .claude/ directory.
    for parent in candidates:
        if (parent / ".claude").is_dir():
            return parent

    return None


def _get_context_path(project_root: Path) -> Path:
    return project_root / ".claude" / "project-context" / "project-context.json"


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_show(args) -> int:
    """Handle `gaia context show [--section SECTION] [--json]`."""
    project_root = _find_project_root(Path.cwd())
    if project_root is None:
        msg = "gaia context: could not find project root (.claude/ directory)"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    context_path = _get_context_path(project_root)
    if not context_path.exists():
        msg = f"project-context.json not found at {context_path}"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Failed to parse project-context.json: {exc}"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    section = getattr(args, "section", None)

    if section:
        sections = data.get("sections", {})
        if section not in sections:
            msg = f"Section '{section}' not found. Available: {', '.join(sorted(sections.keys()))}"
            if getattr(args, "json", False):
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
        section_data = sections[section]
        if getattr(args, "json", False):
            print(json.dumps(section_data, indent=2))
        else:
            print(json.dumps(section_data, indent=2))
        return 0

    # No specific section: print summary
    metadata = data.get("metadata", {})
    sections = data.get("sections", {})

    if getattr(args, "json", False):
        summary = {
            "metadata": metadata,
            "sections": list(sections.keys()),
        }
        print(json.dumps(summary, indent=2))
        return 0

    # Human-readable summary
    version = metadata.get("version", "unknown")
    last_updated = metadata.get("last_updated", "unknown")
    scan_info = metadata.get("scan_config", {})
    last_scan = scan_info.get("last_scan", "unknown")
    scanner_version = scan_info.get("scanner_version", "unknown")

    print(f"project-context  v{version}")
    print(f"  last_updated   : {last_updated}")
    print(f"  last_scan      : {last_scan}")
    print(f"  scanner        : {scanner_version}")
    print()
    print(f"sections ({len(sections)}):")
    for key in sorted(sections.keys()):
        src = sections[key].get("_source", "")
        if src:
            print(f"  {key:<30}  [{src}]")
        else:
            print(f"  {key}")
    return 0


def _cmd_scan(args) -> int:
    """Handle `gaia context scan [--dry-run] [--json]`."""
    project_root = _find_project_root(Path.cwd())
    if project_root is None:
        msg = "gaia context: could not find project root (.claude/ directory)"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    dry_run = getattr(args, "dry_run", False)

    if dry_run:
        # Validate context freshness and report what would be scanned
        context_path = _get_context_path(project_root)
        result = {
            "dry_run": True,
            "project_root": str(project_root),
            "context_path": str(context_path),
            "context_exists": context_path.exists(),
        }
        if context_path.exists():
            try:
                data = json.loads(context_path.read_text(encoding="utf-8"))
                scan_cfg = data.get("metadata", {}).get("scan_config", {})
                result["last_scan"] = scan_cfg.get("last_scan", "unknown")
                result["scanner_version"] = scan_cfg.get("scanner_version", "unknown")
                result["staleness_hours"] = scan_cfg.get("staleness_hours", 24)
                result["would_scan"] = "all scanners (stack, git, infrastructure, environment, orchestration, architecture)"
            except (json.JSONDecodeError, OSError):
                result["would_scan"] = "all scanners (could not read existing context)"
        else:
            result["would_scan"] = "all scanners (no existing context)"

        if getattr(args, "json", False):
            print(json.dumps(result, indent=2))
        else:
            print("[dry-run] Context scan would execute:")
            print(f"  project_root : {result['project_root']}")
            print(f"  context_path : {result['context_path']}")
            print(f"  context_exists: {result['context_exists']}")
            if result.get("last_scan"):
                print(f"  last_scan    : {result['last_scan']}")
            print(f"  would_scan   : {result['would_scan']}")
        return 0

    # Locate gaia-scan.py relative to this script's parent (bin/)
    # bin/cli/context.py -> bin/ -> package_root
    script_dir = Path(__file__).resolve().parent.parent  # bin/
    scan_script = script_dir / "gaia-scan.py"

    if not scan_script.exists():
        msg = f"gaia-scan.py not found at {scan_script}"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(scan_script), "--root", str(project_root)]
    if getattr(args, "json", False):
        cmd.append("--json")

    proc = subprocess.run(cmd)
    return proc.returncode


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `context` subcommand with the root parser."""
    ctx_parser = subparsers.add_parser(
        "context",
        help="Display and refresh project context",
    )
    ctx_subparsers = ctx_parser.add_subparsers(dest="context_cmd", metavar="<action>")

    # gaia context show
    show_parser = ctx_subparsers.add_parser("show", help="Display project-context.json")
    show_parser.add_argument(
        "--section",
        metavar="SECTION",
        default=None,
        help="Show a specific section of project-context.json",
    )
    show_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )

    # gaia context scan
    scan_parser = ctx_subparsers.add_parser(
        "scan", help="Run project scanner (gaia-scan.py)"
    )
    scan_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate context freshness without running scan",
    )
    scan_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )


def cmd_context(args) -> int:
    """Dispatch handler for `gaia context`."""
    context_cmd = getattr(args, "context_cmd", None)
    if context_cmd == "show":
        return _cmd_show(args)
    if context_cmd == "scan":
        return _cmd_scan(args)

    # No sub-action: print help for the context subcommand
    import argparse

    tmp_parser = argparse.ArgumentParser(prog="gaia context")
    tmp_sub = tmp_parser.add_subparsers(dest="context_cmd", metavar="<action>")
    show_p = tmp_sub.add_parser("show", help="Display project-context.json")
    show_p.add_argument("--section", metavar="SECTION")
    tmp_sub.add_parser("scan", help="Run project scanner").add_argument("--dry-run", action="store_true")
    tmp_parser.print_help()
    return 0
