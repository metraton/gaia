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
    gaia brief close <name>               Set status -> closed (advisory: runs
                                          verify_brief and prints inconsistencies;
                                          does NOT change AC/milestone/plan status)
    gaia brief set-status <name> <status> Validated state-machine transition
                                          (DB-only)
    gaia brief deps <name> [--json]       Print dependency graph
    gaia brief search <query> [--limit N] FTS5 search over objective/context/approach
    gaia brief delete <name> [--yes]      Hard-delete a brief from the DB
                  [--json]                (cascades to ACs, milestones, deps)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# File / stdin helpers
# ---------------------------------------------------------------------------

# Ensure the gaia package (repo root) is importable regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _read_content_file(path_str: str) -> str:
    """Read content text from a file path or stdin.

    Pass ``"-"`` to read from ``sys.stdin`` until EOF (utf-8).
    Pass any other path string to read that file (utf-8).
    Raises ``FileNotFoundError`` for missing paths (caller converts to _err).
    """
    if path_str == "-":
        return sys.stdin.read()
    return Path(path_str).read_text(encoding="utf-8")


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

        # Resolve *-file flags for fields that support them.
        def _resolve_field(inline_val, file_attr, field_name):
            """Return inline_val unless a --*-file flag was given."""
            path_str = getattr(args, file_attr, None)
            if path_str is None:
                return inline_val
            try:
                return _read_content_file(path_str)
            except FileNotFoundError:
                raise ValueError(
                    f"--{field_name}-file: file not found: {path_str}"
                )
            except OSError as exc:
                raise ValueError(
                    f"--{field_name}-file: cannot read '{path_str}': {exc}"
                )

        try:
            objective = _resolve_field(
                getattr(args, "objective", None), "objective_file", "objective"
            )
            context_val = _resolve_field(
                getattr(args, "context", None), "context_file", "context"
            )
            approach = _resolve_field(
                getattr(args, "approach", None), "approach_file", "approach"
            )
            out_of_scope = _resolve_field(
                getattr(args, "out_of_scope", None), "out_of_scope_file",
                "out-of-scope"
            )
        except ValueError as exc:
            return _err(str(exc), as_json=as_json)

        fields = {
            "title": title,
            "status": status,
            "surface_type": getattr(args, "surface_type", None),
            "objective": objective,
            "context": context_val,
            "approach": approach,
            "out_of_scope": out_of_scope,
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
    """Edit a brief either interactively ($EDITOR) or via DB-only field patch.

    Headless flow (``--headless --field=... --content="..." [--append]``):
    skips ``$EDITOR`` and the markdown round-trip; instead, writes a single
    column on the brief row using
    :func:`gaia.store.writer.update_brief_field`. The append flag concatenates
    with ``\\n\\n`` separator when the field already has content.

    ``--content-file PATH`` (mutex with ``--content``) reads the content from
    a file.  Use ``-`` to read from stdin.  This is the recommended approach
    for bodies containing angle brackets, nested quotes, or code blocks that
    break shell quoting.

    Interactive flow: opens the brief markdown in ``$EDITOR``; the caller
    saves the file and the parsed result is upserted.
    """
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    as_json = getattr(args, "json", False)

    headless = getattr(args, "headless", False)
    if headless:
        from gaia.store.writer import update_brief_field

        field = getattr(args, "field", None)
        content = getattr(args, "content", None)
        content_file = getattr(args, "content_file", None)
        append = getattr(args, "append", False)

        if not field:
            return _err("--field is required with --headless", as_json=as_json)

        if content_file is not None:
            try:
                content = _read_content_file(content_file)
            except FileNotFoundError:
                return _err(
                    f"--content-file: file not found: {content_file}", as_json=as_json
                )
            except OSError as exc:
                return _err(
                    f"--content-file: cannot read '{content_file}': {exc}",
                    as_json=as_json,
                )

        if content is None or content == "":
            return _err(
                "--content or --content-file is required with --headless",
                as_json=as_json,
            )

        try:
            res = update_brief_field(workspace, name, field, content,
                                     append=append)
        except ValueError as exc:
            return _err(str(exc), as_json=as_json)

        if as_json:
            print(json.dumps(res, indent=2, default=str))
        else:
            print(f"Updated brief '{name}' field={field} action={res['action']}")
        return 0

    from gaia.briefs import (
        parse_brief_markdown,
        serialize_brief_to_markdown,
        upsert_brief,
        get_brief,
    )

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
    from gaia.briefs import (
        get_brief,
        get_brief_by_id,
        find_brief_workspaces,
        serialize_brief_to_markdown,
    )
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    as_json = getattr(args, "json", False)

    # FIX 1: when the argument is all-digits, resolve by numeric id first.
    if name.isdigit():
        brief = get_brief_by_id(int(name))
        if brief is not None:
            if as_json:
                out = {k: v for k, v in brief.items() if k != "id"}
                print(json.dumps(out, indent=2, default=str))
                return 0
            print(serialize_brief_to_markdown(brief))
            return 0
        # Numeric id not found -- fall through to the slug path so that a
        # name that happens to look like a number still gets a useful error.

    brief = get_brief(workspace, name)
    if brief is None:
        # FIX 2: cross-workspace hint instead of bare "not found".
        other_workspaces = find_brief_workspaces(name)
        if other_workspaces:
            hint = ", ".join(repr(w) for w in other_workspaces)
            msg = (
                f"brief '{name}' not found in workspace '{workspace}', "
                f"but exists in: {hint} -- use --workspace=<workspace> to show it"
            )
        else:
            msg = f"brief '{name}' not found in workspace '{workspace}'"
        return _err(msg, as_json=as_json)

    if as_json:
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
    from gaia.briefs.store import verify_brief
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    if close_brief(workspace, name):
        print(f"Closed brief '{name}'")
        # AC-3 advisory: run invariant checker and surface any inconsistencies
        # as non-blocking stderr warnings (mirrors D11 pattern in plan CLI).
        # Close always succeeds (exit 0); warnings never gate the operation.
        try:
            result = verify_brief(workspace, name)
            for issue in result.get("inconsistencies", []):
                print(
                    f"Warning: [{issue['kind']}] {issue['detail']}",
                    file=sys.stderr,
                )
        except Exception:
            pass  # advisory failure must never abort the close
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


def _cmd_delete(args) -> int:
    """Hard-delete a brief from the DB (DB-only, no filesystem touch).

    NOTE (initial implementation): This performs a *hard* delete -- the row
    in ``briefs`` is removed and FK CASCADE wipes acceptance_criteria,
    milestones, brief_dependencies, plans, plan_tasks, and the FTS5 mirror.
    The data is unrecoverable from the DB after commit.

    A follow-up brief is planned for *soft delete* (status=archived or a
    ``deleted_at`` column) so that brief deletion is reversible and audit
    trails are preserved. Until that brief lands, prefer
    ``gaia brief set-status <name> archived`` over ``gaia brief delete``
    when the goal is to retire a brief rather than purge it for testing.
    """
    from gaia.briefs import get_brief, delete_brief
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    as_json = getattr(args, "json", False)
    skip_confirm = getattr(args, "yes", False)

    brief = get_brief(workspace, name)
    if brief is None:
        return _err(
            f"brief '{name}' not found in workspace '{workspace}'",
            as_json=as_json,
        )

    if not skip_confirm:
        status = brief.get("status") or "?"
        prompt = f"Delete brief '{name}' (status={status})? [y/N] "
        try:
            answer = input(prompt)
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            if as_json:
                print(json.dumps({"deleted": False, "name": name,
                                  "reason": "aborted by user"}))
            else:
                print(f"Aborted; brief '{name}' was not deleted.")
            return 0

    deleted = delete_brief(workspace, name)
    if not deleted:
        # Race-condition guard: the brief existed at the get_brief call but
        # someone else removed it before our DELETE landed.
        return _err(
            f"brief '{name}' could not be deleted (already gone?)",
            as_json=as_json,
        )

    if as_json:
        print(json.dumps({
            "deleted": True,
            "name": name,
            "workspace": workspace,
            "previous_status": brief.get("status"),
        }, indent=2, default=str))
    else:
        print(f"Deleted brief '{name}' (workspace='{workspace}', "
              f"previous_status={brief.get('status')!r})")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `brief` subcommand with the root parser."""
    brief_parser = subparsers.add_parser(
        "brief",
        help="Manage briefs (DB-canonical)",
        description=(
            "Create, edit, list, and transition briefs stored in "
            "~/.gaia/gaia.db. All writes are DB-only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    brief_parser.add_argument(
        "--workspace", metavar="W", default=None,
        help="Workspace identity. Default: gaia.project.current() or 'me'.",
    )

    actions = brief_parser.add_subparsers(dest="brief_action", metavar="<action>")

    # -- new ----------------------------------------------------------------
    new_p = actions.add_parser(
        "new",
        help="Create a brief",
        description="Create a new brief. Opens $EDITOR unless --headless.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia brief new my-feature\n"
               "  gaia brief new --headless --title='My Feature' "
               "--objective='...'\n",
    )
    new_p.add_argument("name", nargs="?", default=None,
                       help="Brief slug. Optional with --headless.")
    new_p.add_argument("--workspace", default=None,
                       help="Workspace identity.")
    new_p.add_argument("--headless", action="store_true", default=False,
                       help="Build from flags. bool. Default: false.")
    new_p.add_argument("--title", default=None,
                       help="Title. Required with --headless.")
    new_p.add_argument("--objective", default=None,
                       help="Objective section. str.")
    new_p.add_argument(
        "--objective-file", dest="objective_file", default=None, metavar="PATH",
        help=(
            "Read --objective from PATH. Use '-' to read from stdin. "
            "Useful for content containing angle brackets, code blocks, or "
            "nested quotes that break shell quoting."
        ),
    )
    new_p.add_argument("--context", default=None,
                       help="Context section. str.")
    new_p.add_argument(
        "--context-file", dest="context_file", default=None, metavar="PATH",
        help=(
            "Read --context from PATH. Use '-' to read from stdin."
        ),
    )
    new_p.add_argument("--approach", default=None,
                       help="Approach section. str.")
    new_p.add_argument(
        "--approach-file", dest="approach_file", default=None, metavar="PATH",
        help=(
            "Read --approach from PATH. Use '-' to read from stdin."
        ),
    )
    new_p.add_argument("--out-of-scope", dest="out_of_scope", default=None,
                       help="Out-of-scope section. str.")
    new_p.add_argument(
        "--out-of-scope-file", dest="out_of_scope_file", default=None,
        metavar="PATH",
        help=(
            "Read --out-of-scope from PATH. Use '-' to read from stdin."
        ),
    )
    new_p.add_argument("--status", default=None,
                       choices=("draft", "open", "in-progress", "closed", "archived"),
                       help="Initial status. Default: draft.")
    new_p.add_argument("--surface-type", dest="surface_type", default=None,
                       choices=("ui", "api", "job", "cli"),
                       help="Surface type the brief targets. One of ui|api|job|cli.")
    new_p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON. bool.")

    # -- edit ---------------------------------------------------------------
    edit_p = actions.add_parser(
        "edit",
        help="Edit a brief",
        description=(
            "Edit a brief. Opens $EDITOR; with --headless patches a single "
            "column via --field/--content."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia brief edit my-feature\n"
               "  gaia brief edit my-feature --headless --field=objective "
               "--content='...'\n",
    )
    edit_p.add_argument("name", help="Brief slug.")
    edit_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    edit_p.add_argument("--headless", action="store_true", default=False,
                        help="Patch via flags. bool. Default: false.")
    edit_p.add_argument(
        "--field", default=None,
        choices=("objective", "context", "approach", "out_of_scope",
                 "description", "title", "surface_type"),
        help="Column to patch. Required with --headless. surface_type accepts "
             "ui|api|job|cli.",
    )
    _edit_content_group = edit_p.add_mutually_exclusive_group()
    _edit_content_group.add_argument(
        "--content", default=None,
        help="New value for --field. Required with --headless (or --content-file).",
    )
    _edit_content_group.add_argument(
        "--content-file", dest="content_file", default=None, metavar="PATH",
        help=(
            "Read --content from PATH. Use '-' to read from stdin. "
            "Mutex with --content. Recommended for bodies containing angle "
            "brackets, code blocks, or nested quotes that break shell quoting."
        ),
    )
    edit_p.add_argument("--append", action="store_true", default=False,
                        help="Append (separator '\\n\\n') instead of overwrite. "
                             "bool. Default: false.")
    edit_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON. bool.")

    # -- show ---------------------------------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Print a brief as markdown",
        description="Print the brief as markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia brief show my-feature\n",
    )
    show_p.add_argument("name", help="Brief slug.")
    show_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON. bool.")
    show_p.add_argument("--workspace", default=None,
                        help="Workspace identity.")

    # -- list ---------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List briefs",
        description="List briefs in the workspace, optionally filtered.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia brief list --status=open\n",
    )
    list_p.add_argument("--status", default=None,
                        help="Filter by status. str.")
    list_p.add_argument("--format", default="table",
                        choices=("table", "count", "json"),
                        help="Output shape. Default: table.")
    list_p.add_argument("--workspace", default=None,
                        help="Workspace identity.")

    # -- close --------------------------------------------------------------
    close_p = actions.add_parser(
        "close",
        help="Set brief status to closed (advisory verify, no cascade)",
        description=(
            "Set the brief's status to 'closed', then run verify_brief and "
            "print any inconsistencies as warnings. ADVISORY ONLY: it does NOT "
            "change AC, milestone, or plan status, and performs no cascade. To "
            "resolve a flagged AC, use 'gaia ac set-status' (done / descoped)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia brief close <name>\n  gaia brief close my-feature --workspace=me\n",
    )
    close_p.add_argument("name", help="Brief slug.")
    close_p.add_argument("--workspace", default=None,
                         help="Workspace identity.")

    # -- set-status ---------------------------------------------------------
    setstatus_p = actions.add_parser(
        "set-status",
        help="Transition brief status",
        description="State-machine transition (DB-only).",
    )
    setstatus_p.add_argument("name", help="Brief slug.")
    setstatus_p.add_argument(
        "new_status",
        choices=("draft", "open", "in-progress", "closed", "archived"),
        help="Target status.",
    )
    setstatus_p.add_argument("--workspace", default=None,
                             help="Workspace identity.")
    setstatus_p.add_argument("--json", action="store_true", default=False,
                             help="Emit JSON. bool.")

    # -- deps ---------------------------------------------------------------
    deps_p = actions.add_parser(
        "deps",
        help="Show dependency graph",
        description="Print the brief's dependency graph.",
    )
    deps_p.add_argument("name", help="Brief slug.")
    deps_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON. bool.")
    deps_p.add_argument("--workspace", default=None,
                        help="Workspace identity.")

    # -- search -------------------------------------------------------------
    search_p = actions.add_parser(
        "search",
        help="FTS5 search over briefs",
        description="Full-text search over objective/context/approach.",
    )
    search_p.add_argument("query", help="FTS5 query string.")
    search_p.add_argument("--limit", type=int, default=10,
                          help="Max results. int. Default: 10.")
    search_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON. bool.")
    search_p.add_argument("--workspace", default=None,
                          help="Workspace identity.")

    # -- delete -------------------------------------------------------------
    delete_p = actions.add_parser(
        "delete",
        help="Hard-delete a brief",
        description="Hard-delete a brief and its ACs, milestones, deps.",
    )
    delete_p.add_argument("name", help="Brief slug.")
    delete_p.add_argument("--workspace", default=None,
                          help="Workspace identity.")
    delete_p.add_argument(
        "--yes", action="store_true", default=False,
        help="Skip confirm prompt. bool. Default: false.",
    )
    delete_p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON. bool.",
    )

    # -- verify ---------------------------------------------------------------
    verify_p = actions.add_parser(
        "verify",
        help="Run invariant checks against a brief",
        description=(
            "Detect inconsistencies between brief, plan, tasks, ACs, and "
            "milestones. Returns a structured report; exit code 0 means "
            "no inconsistencies, exit code 2 means inconsistencies found."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia brief verify my-brief\n"
            "  gaia brief verify my-brief --json\n"
        ),
    )
    verify_p.add_argument("name", metavar="NAME", help="Brief slug.")
    verify_p.add_argument("--workspace", default=None, metavar="W",
                          help="Workspace identity.")
    verify_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON.")

    # -- milestone <add|remove> --------------------------------------------
    milestone_p = actions.add_parser(
        "milestone",
        help="Add or remove a milestone row (DB-only)",
        description="Manage milestones for a brief (milestones table).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia brief milestone add my-brief --name=M1 "
            "--description='...'\n"
            "  gaia brief milestone remove my-brief --name=M1\n"
        ),
    )
    m_actions = milestone_p.add_subparsers(
        dest="milestone_action", metavar="<add|remove>"
    )
    m_add = m_actions.add_parser("add", help="Add a milestone")
    m_add.add_argument("brief", help="Brief slug.")
    m_add.add_argument("--name", required=True, help="Milestone name (e.g. M1).")
    m_add.add_argument("--description", default=None, help="Milestone description.")
    m_add.add_argument("--order", type=int, default=None,
                       help="Order number. Auto-assigned (MAX+1) if omitted.")
    m_add.add_argument("--workspace", default=None, metavar="W",
                       help="Workspace identity.")
    m_add.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON.")
    m_rm = m_actions.add_parser("remove", help="Remove a milestone")
    m_rm.add_argument("brief", help="Brief slug.")
    m_rm.add_argument("--name", required=True, help="Milestone name to remove.")
    m_rm.add_argument("--workspace", default=None, metavar="W",
                      help="Workspace identity.")
    m_rm.add_argument("--json", action="store_true", default=False,
                      help="Emit JSON.")

    # -- ac <add|remove> ----------------------------------------------------
    ac_p = actions.add_parser(
        "ac",
        help="Add or remove an acceptance criterion (DB-only)",
        description="Manage acceptance criteria for a brief "
                    "(acceptance_criteria table).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia brief ac add my-brief --id=AC-1 --description='...' "
            "--evidence-type=command --artifact=evidence/AC-1.txt\n"
            "  gaia brief ac remove my-brief --id=AC-1\n"
        ),
    )
    ac_actions = ac_p.add_subparsers(dest="ac_action", metavar="<add|remove>")
    ac_add = ac_actions.add_parser("add", help="Add an acceptance criterion")
    ac_add.add_argument("brief", help="Brief slug.")
    ac_add.add_argument("--id", required=True, help="AC id (e.g. AC-1).")
    ac_add.add_argument("--description", default=None, help="AC description.")
    ac_add.add_argument(
        "--evidence-type", dest="evidence_type", default=None,
        help="Evidence type (e.g. command, file, url, screenshot).",
    )
    ac_add.add_argument(
        "--evidence-shape", dest="evidence_shape", default=None,
        help="Evidence shape hint (free-form string or JSON).",
    )
    ac_add.add_argument(
        "--artifact", dest="artifact", default=None,
        help="Relative artifact path (e.g. evidence/AC-1.txt).",
    )
    ac_add.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    ac_add.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON.")
    ac_rm = ac_actions.add_parser("remove", help="Remove an acceptance criterion")
    ac_rm.add_argument("brief", help="Brief slug.")
    ac_rm.add_argument("--id", required=True, help="AC id to remove.")
    ac_rm.add_argument("--workspace", default=None, metavar="W",
                       help="Workspace identity.")
    ac_rm.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON.")


def _cmd_verify(args) -> int:
    from gaia.briefs.store import verify_brief

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    name = args.name
    as_json = getattr(args, "json", False)

    try:
        result = verify_brief(workspace, name)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result["pass"]:
            print(f"Brief '{name}': OK (no inconsistencies)")
        else:
            print(f"Brief '{name}': {len(result['inconsistencies'])} "
                  f"inconsistencies detected")
            for issue in result["inconsistencies"]:
                print(f"  - [{issue['kind']}] {issue['detail']}")

    return 0 if result["pass"] else 2


def _cmd_milestone(args) -> int:
    """Dispatch `gaia brief milestone <add|remove>` -- milestones table writes.

    Local planning bookkeeping (reversible, DB-only), covered by the
    ("gaia","brief") tier exemption -- no T3 approval. `remove` is a single-row
    delete (not whole-record destruction), so it stays exempt too.
    """
    from gaia.briefs import add_milestone, remove_milestone

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)
    sub = getattr(args, "milestone_action", None)
    brief_name = getattr(args, "brief", None)
    m_name = getattr(args, "name", None)

    if sub not in ("add", "remove"):
        return _err("usage: gaia brief milestone <add|remove>", as_json=as_json)
    if not brief_name:
        return _err("brief slug is required", as_json=as_json)
    if not m_name:
        return _err("--name is required", as_json=as_json)

    try:
        if sub == "add":
            res = add_milestone(
                workspace, brief_name, m_name,
                description=getattr(args, "description", None),
                order_num=getattr(args, "order", None),
            )
        else:
            res = remove_milestone(workspace, brief_name, m_name)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)
    except PermissionError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Milestone '{m_name}' {res['action']} in brief '{brief_name}'")
    return 0


def _cmd_ac(args) -> int:
    """Dispatch `gaia brief ac <add|remove>` -- acceptance_criteria table writes.

    Local planning bookkeeping (reversible, DB-only), covered by the
    ("gaia","brief") tier exemption -- no T3 approval. `remove` is a single-row
    delete, so it stays exempt.
    """
    from gaia.briefs import add_ac, remove_ac

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)
    sub = getattr(args, "ac_action", None)
    brief_name = getattr(args, "brief", None)
    ac_id = getattr(args, "id", None)

    if sub not in ("add", "remove"):
        return _err("usage: gaia brief ac <add|remove>", as_json=as_json)
    if not brief_name:
        return _err("brief slug is required", as_json=as_json)
    if not ac_id:
        return _err("--id is required", as_json=as_json)

    try:
        if sub == "add":
            res = add_ac(
                workspace, brief_name, ac_id,
                description=getattr(args, "description", None),
                evidence_type=getattr(args, "evidence_type", None),
                evidence_shape=getattr(args, "evidence_shape", None),
                artifact_path=getattr(args, "artifact", None),
            )
        else:
            res = remove_ac(workspace, brief_name, ac_id)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)
    except PermissionError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"AC '{ac_id}' {res['action']} in brief '{brief_name}'")
    return 0


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
        "delete": _cmd_delete,
        "verify": _cmd_verify,
        "milestone": _cmd_milestone,
        "ac": _cmd_ac,
    }
    if action in handlers:
        return handlers[action](args)

    print(
        "Usage: gaia brief "
        "<new|edit|show|list|close|set-status|deps|search|delete|verify|"
        "milestone|ac>",
        file=sys.stderr,
    )
    return 0
