"""
gaia plans -- List and display project briefs/plans.

Subcommands:
  gaia plans list [--json]              List all briefs with status info
  gaia plans show <name> [--json]       Show brief.md + plan.md for a named brief
                                        (accepts name with or without prefix)
  gaia plans rename <name> [--all]      Sync directory prefix to frontmatter status
                                        (accepts name with or without prefix)
"""

from __future__ import annotations

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
# Prefix helpers
# ---------------------------------------------------------------------------

_KNOWN_PREFIXES = ("open_", "in-progress_", "closed_")

_STATUS_TO_PREFIX: dict[str, str] = {
    "draft": "open_",
    "ready": "open_",
    "in-progress": "in-progress_",
    "complete": "closed_",
    "verified": "closed_",
    "done": "closed_",
}


def _strip_prefix(name: str) -> str:
    """Return the bare feature name without any known prefix."""
    for prefix in _KNOWN_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _resolve_brief_dir(briefs_dir: Path, name: str) -> Path | None:
    """Find the brief directory for ``name`` regardless of prefix.

    If ``name`` already contains a valid prefix, look for that exact path first.
    Otherwise (or if not found), search by stripping all known prefixes from
    existing directories and matching the bare suffix.

    Returns the Path if a unique match is found, None if not found.
    Raises ValueError if multiple directories match the same bare name.
    """
    if not briefs_dir.is_dir():
        return None

    # Exact match first (name may already have the right prefix).
    exact = briefs_dir / name
    if exact.is_dir():
        return exact

    # Fuzzy match: strip prefix from ``name``, then compare bare suffixes.
    bare = _strip_prefix(name)
    matches = [
        entry for entry in briefs_dir.iterdir()
        if entry.is_dir() and _strip_prefix(entry.name) == bare
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous brief name '{name}': multiple matches {[m.name for m in matches]}"
        )
    return None


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
                "plan_file_status": plan_fm.get("status", "(absent)") if plan_file.exists() else "(absent)",
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
    col_plan = max(len("PLAN STATUS"), max(len(b["plan_file_status"]) for b in briefs))

    header = (
        f"{'BRIEF':<{col_name}}  {'BRIEF STATUS':<{col_brief}}  {'PLAN STATUS':<{col_plan}}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)
    for b in briefs:
        print(
            f"{b['name']:<{col_name}}  {b['brief_status']:<{col_brief}}  {b['plan_file_status']:<{col_plan}}"
        )
    return 0


def _cmd_show(args) -> int:
    """Handle `gaia plans show <name>` (prefix-tolerant)."""
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

    try:
        brief_dir = _resolve_brief_dir(briefs_dir, brief_name)
    except ValueError as exc:
        msg = str(exc)
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    if brief_dir is None:
        msg = f"Brief '{brief_name}' not found in {briefs_dir}"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    # Use the resolved directory name for display.
    brief_name = brief_dir.name

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


def _rename_one(briefs_dir: Path, name: str) -> dict:
    """Rename a single brief directory to match its frontmatter status.

    Returns a result dict with keys: old_name, new_name, status, action.
    action is "renamed" or "already-correct".
    Raises ValueError on ambiguous match or missing brief.
    """
    brief_dir = _resolve_brief_dir(briefs_dir, name)
    if brief_dir is None:
        raise ValueError(f"Brief '{name}' not found in {briefs_dir}")

    brief_file = brief_dir / "brief.md"
    if not brief_file.exists():
        raise ValueError(f"brief.md not found in '{brief_dir.name}'")

    brief_fm = _parse_frontmatter(brief_file.read_text(encoding="utf-8"))
    status = brief_fm.get("status", "")

    expected_prefix = _STATUS_TO_PREFIX.get(status)
    if expected_prefix is None:
        raise ValueError(
            f"Unknown status '{status}' in '{brief_dir.name}'. "
            f"Known values: {sorted(_STATUS_TO_PREFIX)}"
        )

    bare = _strip_prefix(brief_dir.name)
    expected_name = expected_prefix + bare

    if brief_dir.name == expected_name:
        return {
            "old_name": brief_dir.name,
            "new_name": brief_dir.name,
            "status": status,
            "action": "already-correct",
        }

    new_dir = briefs_dir / expected_name
    brief_dir.rename(new_dir)
    return {
        "old_name": brief_dir.name,
        "new_name": expected_name,
        "status": status,
        "action": "renamed",
    }


def _cmd_rename(args) -> int:
    """Handle `gaia plans rename <name>` and `gaia plans rename --all`."""
    project_root = _find_project_root(Path.cwd())
    if project_root is None:
        msg = "gaia plans: could not find project root (.claude/ directory)"
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    briefs_dir = _get_briefs_dir(project_root)

    rename_all = getattr(args, "all", False)

    if rename_all:
        if not briefs_dir.is_dir():
            result: dict = {"results": [], "error": None}
            if getattr(args, "json", False):
                print(json.dumps(result, indent=2))
            else:
                print("No briefs directory found.")
            return 0

        results = []
        errors = []
        for entry in sorted(briefs_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "brief.md").exists():
                continue
            try:
                res = _rename_one(briefs_dir, entry.name)
                results.append(res)
            except ValueError as exc:
                errors.append({"name": entry.name, "error": str(exc)})

        if getattr(args, "json", False):
            print(json.dumps({"results": results, "errors": errors}, indent=2))
        else:
            for res in results:
                action_label = "renamed" if res["action"] == "renamed" else "ok"
                print(f"[{action_label}] {res['old_name']} -> {res['new_name']}  (status: {res['status']})")
            for err in errors:
                print(f"[error] {err['name']}: {err['error']}", file=sys.stderr)
        return 0

    # Single brief rename.
    brief_name: str = getattr(args, "name", None)
    if not brief_name:
        print("Error: provide a brief name or use --all", file=sys.stderr)
        return 1

    try:
        result_single = _rename_one(briefs_dir, brief_name)
    except ValueError as exc:
        msg = str(exc)
        if getattr(args, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result_single, indent=2))
    else:
        if result_single["action"] == "renamed":
            print(
                f"Renamed: {result_single['old_name']} -> {result_single['new_name']} "
                f"(status: {result_single['status']})"
            )
        else:
            print(
                f"Already correct: {result_single['new_name']} "
                f"(status: {result_single['status']})"
            )
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
    show_parser = plans_subparsers.add_parser(
        "show", help="Show brief content (prefix-tolerant)"
    )
    show_parser.add_argument(
        "name", help="Brief name with or without prefix (e.g. evidence-runner or open_evidence-runner)"
    )
    show_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output as JSON",
    )

    # gaia plans rename <name> [--all]
    rename_parser = plans_subparsers.add_parser(
        "rename", help="Sync directory prefix to frontmatter status"
    )
    rename_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Brief name with or without prefix. Omit when using --all.",
    )
    rename_parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Sync all briefs in the briefs directory",
    )
    rename_parser.add_argument(
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
    if plans_cmd == "rename":
        return _cmd_rename(args)

    # No sub-action: print help for the plans subcommand
    import argparse

    # Re-parse with just `plans --help` to show the sub-help
    tmp_parser = argparse.ArgumentParser(prog="gaia plans")
    tmp_sub = tmp_parser.add_subparsers(dest="plans_cmd", metavar="<action>")
    tmp_sub.add_parser("list", help="List all briefs")
    show_p = tmp_sub.add_parser("show", help="Show brief content")
    show_p.add_argument("name")
    rename_p = tmp_sub.add_parser("rename", help="Sync directory prefix to frontmatter status")
    rename_p.add_argument("name", nargs="?")
    rename_p.add_argument("--all", action="store_true")
    tmp_parser.print_help()
    return 0
