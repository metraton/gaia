"""
gaia brief -- Manage briefs (and their plans) in the Gaia DB substrate (B8).

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``; nothing under ``.claude/project-context/briefs/`` is ever
created or modified by this CLI. The filesystem layout there is legacy /
read-only-for-humans; the database is the single source of truth.

Subcommands:
    gaia brief new <name>                 Create a new brief (opens $EDITOR)
    gaia brief new --headless --title=... Create a new brief from flags (no EDITOR,
                                          DB-only)
    gaia brief edit <name>                Edit an existing brief in $EDITOR
    gaia brief show <name> [--json]       Print brief as markdown
    gaia brief list [--status=...]        List briefs in the workspace
                  [--format=table|count|json]
    gaia brief close <name>               Set status -> closed
    gaia brief set-status <name> <status> Validated state-machine transition
                                          (DB-only)
    gaia brief deps <name> [--json]       Print dependency graph
    gaia brief search <query> [--limit N] FTS5 search over objective/context/approach
    gaia brief import-from-fs --source PATH [--workspace W]
                                          One-time migration of brief.md files
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure the gaia package (repo root) is importable regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _resolve_workspace(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from gaia.project import current as _project_current
        ws = _project_current()
        if ws:
            return ws
    except Exception:
        pass
    return "me"


# ---------------------------------------------------------------------------
# Editor round-trip
# ---------------------------------------------------------------------------

_BRIEF_TEMPLATE = """\
---
status: draft
surface_type: cli
acceptance_criteria: []
---

# {name}

## Objective


## Context


## Approach


## Out of Scope


"""


def _slugify(title: str) -> str:
    """Derive a URL/filename-safe slug from a brief title.

    Lowercases, replaces non-alphanumeric runs with single hyphens, strips
    leading/trailing hyphens. Empty result raises ValueError so callers fail
    loudly instead of silently inserting a blank-named row.
    """
    import re as _re
    s = (title or "").strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s:
        raise ValueError("cannot derive slug from empty/symbolic title")
    return s


def _open_in_editor(initial_text: str) -> str:
    """Write initial_text to a temp .md file, open $EDITOR, return result."""
    editor = os.environ.get("EDITOR") or "vi"
    fd, path = tempfile.mkstemp(suffix=".md", prefix="gaia-brief-", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(initial_text)
        subprocess.call([editor, path])
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _err(msg: str, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def _cmd_new(args) -> int:
    from gaia.briefs import (
        parse_brief_markdown,
        upsert_brief,
        get_brief,
        VALID_STATUSES,
    )
    workspace = _resolve_workspace(getattr(args, "workspace", None))

    headless = getattr(args, "headless", False)
    as_json = getattr(args, "json", False)

    if headless:
        # DB-only flow: no $EDITOR, no filesystem, slug derived from --title.
        title = getattr(args, "title", None)
        if not title:
            return _err("--title is required with --headless", as_json=as_json)
        try:
            name = _slugify(title)
        except ValueError as exc:
            return _err(str(exc), as_json=as_json)

        status = getattr(args, "status", None) or "draft"
        if status not in VALID_STATUSES:
            return _err(
                f"invalid status '{status}'; must be one of {list(VALID_STATUSES)}",
                as_json=as_json,
            )

        existing = get_brief(workspace, name)
        if existing is not None:
            return _err(
                f"brief '{name}' already exists in workspace '{workspace}'",
                as_json=as_json,
            )

        fields = {
            "title": title,
            "status": status,
            "objective": getattr(args, "objective", None),
            "context": getattr(args, "context", None),
            "approach": getattr(args, "approach", None),
            "out_of_scope": getattr(args, "out_of_scope", None),
        }
        # Strip None values so DEFAULTs in upsert_brief / NULL columns stay clean.
        fields = {k: v for k, v in fields.items() if v is not None}

        res = upsert_brief(workspace, name, fields)
        brief = get_brief(workspace, name)

        if as_json:
            out = {k: v for k, v in (brief or {}).items() if k != "id"}
            out["_action"] = "created"
            print(json.dumps(out, indent=2, default=str))
        else:
            print(f"Created brief '{name}' (id={res['brief_id']}, "
                  f"status={status}, title={title!r})")
        return 0

    # Interactive flow (legacy): requires positional <name> and opens $EDITOR.
    name = getattr(args, "name", None)
    if not name:
        return _err("name is required (or use --headless --title=...)",
                    as_json=as_json)

    existing = get_brief(workspace, name)
    if existing is not None:
        return _err(f"brief '{name}' already exists in workspace '{workspace}'",
                    as_json=as_json)

    template = _BRIEF_TEMPLATE.format(name=name)
    text = _open_in_editor(template)
    if not text.strip():
        return _err("editor returned empty content; aborted", as_json=as_json)
    try:
        parsed = parse_brief_markdown(text)
    except Exception as exc:
        return _err(f"failed to parse brief: {exc}", as_json=as_json)

    res = upsert_brief(workspace, name, parsed)
    print(f"Created brief '{name}' (id={res['brief_id']}, "
          f"acs={res['acs']}, milestones={res['milestones']})")
    return 0


def _cmd_set_status(args) -> int:
    """Validated state-machine transition. DB-only (no filesystem touch)."""
    from gaia.briefs import set_status_brief
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    new_status = args.new_status
    as_json = getattr(args, "json", False)

    try:
        res = set_status_brief(workspace, name, new_status)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        if res.get("action") == "noop":
            print(f"Brief '{name}' already at status '{new_status}' (noop)")
        else:
            print(f"Brief '{name}': {res['old_status']} -> {res['new_status']}")
    return 0


def _cmd_edit(args) -> int:
    from gaia.briefs import (
        parse_brief_markdown,
        serialize_brief_to_markdown,
        upsert_brief,
        get_brief,
    )
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name

    brief = get_brief(workspace, name)
    if brief is None:
        return _err(f"brief '{name}' not found in workspace '{workspace}'")

    initial = serialize_brief_to_markdown(brief)
    text = _open_in_editor(initial)
    if not text.strip():
        return _err("editor returned empty content; aborted")
    try:
        parsed = parse_brief_markdown(text)
    except Exception as exc:
        return _err(f"failed to parse edited brief: {exc}")

    res = upsert_brief(workspace, name, parsed)
    print(f"Updated brief '{name}' (id={res['brief_id']}, "
          f"acs={res['acs']}, milestones={res['milestones']})")
    return 0


def _cmd_show(args) -> int:
    from gaia.briefs import get_brief, serialize_brief_to_markdown
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name

    brief = get_brief(workspace, name)
    if brief is None:
        return _err(f"brief '{name}' not found in workspace '{workspace}'",
                    as_json=getattr(args, "json", False))

    if getattr(args, "json", False):
        # Drop internal SQL columns for cleanliness
        out = {k: v for k, v in brief.items() if k != "id"}
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(serialize_brief_to_markdown(brief))
    return 0


def _cmd_list(args) -> int:
    from gaia.briefs import list_briefs
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    status = getattr(args, "status", None)
    fmt = getattr(args, "format", None) or "table"

    briefs = list_briefs(workspace, status=status)

    if fmt == "count":
        print(len(briefs))
        return 0
    if fmt == "json":
        print(json.dumps(briefs, indent=2, default=str))
        return 0

    # table
    if not briefs:
        print("(no briefs)")
        return 0
    name_w = max(4, max(len(b["name"]) for b in briefs))
    status_w = max(6, max(len((b["status"] or "")) for b in briefs))
    title_w = max(5, max(len((b.get("title") or "")) for b in briefs))
    print(f"{'NAME':<{name_w}}  {'STATUS':<{status_w}}  {'TITLE':<{title_w}}")
    print("-" * (name_w + status_w + title_w + 4))
    for b in briefs:
        print(f"{b['name']:<{name_w}}  {(b['status'] or ''):<{status_w}}  "
              f"{(b.get('title') or ''):<{title_w}}")
    return 0


def _cmd_close(args) -> int:
    from gaia.briefs import close_brief
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    if close_brief(workspace, name):
        print(f"Closed brief '{name}'")
        return 0
    return _err(f"brief '{name}' not found in workspace '{workspace}'")


def _cmd_deps(args) -> int:
    from gaia.briefs import get_dependencies, get_brief
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name

    if get_brief(workspace, name) is None:
        return _err(f"brief '{name}' not found in workspace '{workspace}'",
                    as_json=getattr(args, "json", False))

    deps = get_dependencies(workspace, name)

    if getattr(args, "json", False):
        print(json.dumps({"brief": name, "dependencies": deps}, indent=2))
        return 0

    if not deps:
        print(f"{name}: no dependencies")
        return 0
    print(f"{name}")
    for d in deps:
        indent = "  " * d["depth"]
        print(f"{indent}-> {d['name']}")
    return 0


def _cmd_search(args) -> int:
    from gaia.briefs import search_briefs
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    query = args.query
    limit = getattr(args, "limit", 10)

    results = search_briefs(workspace, query, limit=limit)

    if getattr(args, "json", False):
        print(json.dumps({"query": query, "results": results}, indent=2))
        return 0

    if not results:
        print(f"(no matches for '{query}')")
        return 0
    for r in results:
        print(f"[{r['rank']:.4f}] {r['name']} -- {r.get('title') or '(no title)'}")
        if r.get("snippet"):
            print(f"   {r['snippet']}")
    return 0


def _cmd_import_from_fs(args) -> int:
    from gaia.briefs import import_from_fs
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    source = getattr(args, "source", None)
    if not source:
        return _err("--source is required")
    res = import_from_fs(source, workspace=workspace)
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2, default=str))
        return 0
    print(f"Imported {res['imported']} briefs into workspace '{workspace}'")
    for d in res.get("details", [])[:20]:
        print(f"  - {d['name']} (acs={d['acs']}, milestones={d['milestones']})")
    if res.get("errors"):
        print(f"Errors: {len(res['errors'])}")
        for e in res["errors"][:10]:
            print(f"  ! {e.get('name', '?')}: {e.get('error', '?')}")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `brief` subcommand with the root parser."""
    brief_parser = subparsers.add_parser(
        "brief",
        help="Manage briefs in the Gaia DB substrate (B8)",
    )
    brief_parser.add_argument(
        "--workspace", metavar="W", default=None,
        help="Workspace identity (default: gaia.project.current() or 'me')",
    )

    actions = brief_parser.add_subparsers(dest="brief_action", metavar="<action>")

    new_p = actions.add_parser(
        "new",
        help="Create a new brief ($EDITOR by default; --headless for flag-driven)",
    )
    new_p.add_argument("name", nargs="?", default=None,
                       help="Brief slug (omitted in --headless mode)")
    new_p.add_argument("--workspace", default=None)
    new_p.add_argument("--headless", action="store_true", default=False,
                       help="Skip $EDITOR; build the brief from CLI flags only "
                            "(DB-only, no filesystem side effects)")
    new_p.add_argument("--title", default=None,
                       help="Title (required with --headless; slug is derived from it)")
    new_p.add_argument("--objective", default=None)
    new_p.add_argument("--context", default=None)
    new_p.add_argument("--approach", default=None)
    new_p.add_argument("--out-of-scope", dest="out_of_scope", default=None)
    new_p.add_argument("--status", default=None,
                       choices=("draft", "open", "in-progress", "closed", "archived"),
                       help="Initial status (default: draft)")
    new_p.add_argument("--json", action="store_true", default=False,
                       help="Emit the new brief as JSON")

    edit_p = actions.add_parser("edit", help="Edit a brief in $EDITOR")
    edit_p.add_argument("name")
    edit_p.add_argument("--workspace", default=None)

    show_p = actions.add_parser("show", help="Print a brief as markdown")
    show_p.add_argument("name")
    show_p.add_argument("--json", action="store_true", default=False)
    show_p.add_argument("--workspace", default=None)

    list_p = actions.add_parser("list", help="List briefs in the workspace")
    list_p.add_argument("--status", default=None,
                        help="Filter by status (e.g. draft, open, closed)")
    list_p.add_argument("--format", default="table",
                        choices=("table", "count", "json"))
    list_p.add_argument("--workspace", default=None)

    close_p = actions.add_parser("close", help="Set brief status to closed")
    close_p.add_argument("name")
    close_p.add_argument("--workspace", default=None)

    setstatus_p = actions.add_parser(
        "set-status",
        help="Validated state-machine transition (DB-only, no filesystem)",
    )
    setstatus_p.add_argument("name")
    setstatus_p.add_argument(
        "new_status",
        choices=("draft", "open", "in-progress", "closed", "archived"),
        help="Target status; the transition is validated against the state machine",
    )
    setstatus_p.add_argument("--workspace", default=None)
    setstatus_p.add_argument("--json", action="store_true", default=False)

    deps_p = actions.add_parser("deps", help="Show dependency graph")
    deps_p.add_argument("name")
    deps_p.add_argument("--json", action="store_true", default=False)
    deps_p.add_argument("--workspace", default=None)

    search_p = actions.add_parser("search", help="FTS5 search over briefs")
    search_p.add_argument("query")
    search_p.add_argument("--limit", type=int, default=10)
    search_p.add_argument("--json", action="store_true", default=False)
    search_p.add_argument("--workspace", default=None)

    import_p = actions.add_parser(
        "import-from-fs",
        help="Migrate brief.md files from a directory tree into the DB",
    )
    import_p.add_argument("--source", required=True)
    import_p.add_argument("--workspace", default=None)
    import_p.add_argument("--json", action="store_true", default=False)


def cmd_brief(args) -> int:
    """Dispatch handler for `gaia brief`."""
    action = getattr(args, "brief_action", None)
    handlers = {
        "new": _cmd_new,
        "edit": _cmd_edit,
        "show": _cmd_show,
        "list": _cmd_list,
        "close": _cmd_close,
        "set-status": _cmd_set_status,
        "deps": _cmd_deps,
        "search": _cmd_search,
        "import-from-fs": _cmd_import_from_fs,
    }
    if action in handlers:
        return handlers[action](args)

    print(
        "Usage: gaia brief "
        "<new|edit|show|list|close|set-status|deps|search|import-from-fs>",
        file=sys.stderr,
    )
    return 0
