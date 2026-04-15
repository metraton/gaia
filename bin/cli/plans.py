"""
gaia plans -- List and display project briefs/plans.

Subcommands:
  gaia plans list [--json]          List all briefs with status info
  gaia plans show <name> [--json]   Show brief.md + plan.md for a named brief
"""

import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Root detection
# ---------------------------------------------------------------------------

def _find_project_root(start: Path) -> Path | None:
    """Locate the project root that owns .claude/project-context/briefs/.

    Resolution order:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code at runtime) -- its
       parent is the project root.
    2. Walk up from ``start`` looking for .claude/project-context/briefs/
       that actually exists (contains user brief data, not plugin config).
    3. Walk up from ``start`` looking for .claude/project-context/ (has
       project-context data even if briefs/ is absent).
    4. Walk up from ``start`` for any .claude/ directory (original fallback).

    Strategies 2-3 ensure the CLI skips a plugin's own .claude/ config dir
    (e.g., gaia-ops-dev/.claude/) and continues up to the user's project root
    (e.g., ~/ws/me/.claude/) when the CLI is invoked from inside the plugin
    subdirectory.
    """
    import os
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        candidate = Path(plugin_data)
        # CLAUDE_PLUGIN_DATA points to .claude/ itself; its parent is the root.
        if candidate.is_dir():
            return candidate.parent
        # If the path doesn't exist yet, still trust the env var.
        return candidate.parent

    current = start.resolve()
    candidates = [current, *current.parents]

    # Pass 1: prefer a root that has the actual briefs data directory.
    for parent in candidates:
        if (parent / ".claude" / "project-context" / "briefs").is_dir():
            return parent

    # Pass 2: accept any root that has project-context/ (data dir present).
    for parent in candidates:
        if (parent / ".claude" / "project-context").is_dir():
            return parent

    # Pass 3: original fallback -- any .claude/ directory.
    for parent in candidates:
        if (parent / ".claude").is_dir():
            return parent

    return None


def _get_briefs_dir(project_root: Path) -> Path:
    return project_root / ".claude" / "project-context" / "briefs"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict:
    """Extract key: value pairs from YAML frontmatter (no external deps).

    Returns an empty dict if no frontmatter found.
    """
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("---", 3)
    except ValueError:
        return {}
    fm_text = text[3:end]
    result = {}
    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                result[key] = value
    return result


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _collect_briefs(briefs_dir: Path) -> list[dict]:
    """Walk briefs_dir and return a list of brief info dicts."""
    results = []
    if not briefs_dir.is_dir():
        return results

    for entry in sorted(briefs_dir.iterdir()):
        if not entry.is_dir():
            continue
        brief_file = entry / "brief.md"
        plan_file = entry / "plan.md"
        if not brief_file.exists():
            continue

        brief_text = brief_file.read_text(encoding="utf-8")
        brief_fm = _parse_frontmatter(brief_text)

        plan_fm: dict = {}
        if plan_file.exists():
            plan_text = plan_file.read_text(encoding="utf-8")
            plan_fm = _parse_frontmatter(plan_text)

        results.append(
            {
                "name": entry.name,
                "brief_status": brief_fm.get("status", "(none)"),
                "plan_status": plan_fm.get("status", "(absent)") if plan_file.exists() else "(absent)",
                "has_plan": plan_file.exists(),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_list(args) -> int:
    """Handle `gaia plans list`."""
    project_root = _find_project_root(Path.cwd())
    if project_root is None:
        msg = "gaia plans: could not find project root (.claude/ directory)"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    briefs_dir = _get_briefs_dir(project_root)
    briefs = _collect_briefs(briefs_dir)

    if getattr(args, "json", False):
        print(json.dumps({"briefs": briefs}, indent=2))
        return 0

    if not briefs:
        print("No briefs found.")
        return 0

    # Human-readable table
    col_name = max(len("BRIEF"), max(len(b["name"]) for b in briefs))
    col_brief = max(len("BRIEF STATUS"), max(len(b["brief_status"]) for b in briefs))
    col_plan = max(len("PLAN STATUS"), max(len(b["plan_status"]) for b in briefs))

    header = (
        f"{'BRIEF':<{col_name}}  {'BRIEF STATUS':<{col_brief}}  {'PLAN STATUS':<{col_plan}}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)
    for b in briefs:
        print(
            f"{b['name']:<{col_name}}  {b['brief_status']:<{col_brief}}  {b['plan_status']:<{col_plan}}"
        )
    return 0


def _cmd_show(args) -> int:
    """Handle `gaia plans show <name>`."""
    project_root = _find_project_root(Path.cwd())
    if project_root is None:
        msg = "gaia plans: could not find project root (.claude/ directory)"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    brief_name: str = args.name
    briefs_dir = _get_briefs_dir(project_root)
    brief_dir = briefs_dir / brief_name

    if not brief_dir.is_dir():
        msg = f"Brief '{brief_name}' not found in {briefs_dir}"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    brief_file = brief_dir / "brief.md"
    plan_file = brief_dir / "plan.md"

    if not brief_file.exists():
        msg = f"brief.md not found for '{brief_name}'"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    brief_content = brief_file.read_text(encoding="utf-8")
    plan_content = plan_file.read_text(encoding="utf-8") if plan_file.exists() else None

    if getattr(args, "json", False):
        payload: dict = {"name": brief_name, "brief": brief_content}
        if plan_content is not None:
            payload["plan"] = plan_content
        print(json.dumps(payload, indent=2))
        return 0

    # Human-readable output
    print(f"=== {brief_name}/brief.md ===")
    print(brief_content)
    if plan_content is not None:
        print(f"=== {brief_name}/plan.md ===")
        print(plan_content)
    else:
        print(f"(no plan.md for '{brief_name}')")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `plans` subcommand with the root parser."""
    plans_parser = subparsers.add_parser(
        "plans",
        help="List and display project briefs/plans",
    )
    plans_subparsers = plans_parser.add_subparsers(dest="plans_cmd", metavar="<action>")

    # gaia plans list
    list_parser = plans_subparsers.add_parser("list", help="List all briefs")
    list_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )

    # gaia plans show <name>
    show_parser = plans_subparsers.add_parser("show", help="Show brief content")
    show_parser.add_argument("name", help="Brief name (directory name under briefs/)")
    show_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )


def cmd_plans(args) -> int:
    """Dispatch handler for `gaia plans`."""
    plans_cmd = getattr(args, "plans_cmd", None)
    if plans_cmd == "list":
        return _cmd_list(args)
    if plans_cmd == "show":
        return _cmd_show(args)

    # No sub-action: print help for the plans subcommand
    import argparse

    # Re-parse with just `plans --help` to show the sub-help
    tmp_parser = argparse.ArgumentParser(prog="gaia plans")
    tmp_sub = tmp_parser.add_subparsers(dest="plans_cmd", metavar="<action>")
    tmp_sub.add_parser("list", help="List all briefs")
    show_p = tmp_sub.add_parser("show", help="Show brief content")
    show_p.add_argument("name")
    tmp_parser.print_help()
    return 0
