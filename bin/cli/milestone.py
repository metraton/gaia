"""
gaia milestone -- Manage milestones for briefs in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``.

Subcommands:
    gaia milestone set-status <brief> <name> <status>  Transition milestone status
                              [--workspace W] [--json]
    gaia milestone add <brief> <name>                  Add a milestone to a brief
                       [--description "..."]
                       [--order=N]
                       [--workspace W] [--json]
    gaia milestone remove <brief> <name>               Remove a milestone
                          [--workspace W] [--json]
    gaia milestone edit <brief> <name>                 Edit a milestone
                        [--new-name "..."]
                        [--description "..."]
                        [--workspace W] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the gaia package (repo root) is importable regardless of cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
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


def _err(msg: str, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_set_status(args) -> int:
    from gaia.store.writer import set_milestone_status
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    milestone_name = args.name
    new_status = args.status
    as_json = getattr(args, "json", False)

    try:
        res = set_milestone_status(workspace, brief_name, milestone_name, new_status,
                                   db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        if res.get("action") == "noop":
            print(f"Milestone '{milestone_name}' in '{brief_name}' already at "
                  f"status '{new_status}' (noop)")
        else:
            print(f"Milestone '{milestone_name}' in '{brief_name}': "
                  f"{res['old_status']} -> {res['new_status']}")
    return 0


def _cmd_add(args) -> int:
    from gaia.briefs.store import add_milestone
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    name = args.name
    description = getattr(args, "description", None)
    order_num = getattr(args, "order", None)
    as_json = getattr(args, "json", False)

    try:
        res = add_milestone(
            workspace, brief_name, name,
            description=description,
            order_num=order_num,
            db_path=None,
        )
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Added milestone '{name}' to brief '{brief_name}'")
    return 0


def _cmd_remove(args) -> int:
    from gaia.briefs.store import remove_milestone
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    name = args.name
    as_json = getattr(args, "json", False)

    try:
        res = remove_milestone(workspace, brief_name, name, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Removed milestone '{name}' from brief '{brief_name}'")
    return 0


def _cmd_edit(args) -> int:
    from gaia.briefs.store import update_milestone
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    name = args.name
    new_name = getattr(args, "new_name", None)
    description = getattr(args, "description", None)
    as_json = getattr(args, "json", False)

    if all(v is None for v in [new_name, description]):
        return _err(
            "At least one of --new-name or --description is required for edit",
            as_json=as_json,
        )

    try:
        res = update_milestone(
            workspace, brief_name, name,
            new_name=new_name,
            description=description,
            db_path=None,
        )
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Updated milestone '{name}' in brief '{brief_name}'")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `milestone` subcommand with the root parser."""
    ms_parser = subparsers.add_parser(
        "milestone",
        help="Manage milestones for briefs (DB-canonical)",
        description=(
            "Transition milestone status and add/remove/edit individual "
            "milestones without full-sync destruction."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ms_parser.add_argument(
        "--workspace", metavar="W", default=None,
        help="Workspace identity. Default: gaia.project.current() or 'me'.",
    )

    actions = ms_parser.add_subparsers(dest="milestone_action", metavar="<action>")

    # -- set-status ------------------------------------------------------------
    setstatus_p = actions.add_parser(
        "set-status",
        help="Transition a milestone's status",
        description="Validate and apply a milestone status transition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia milestone set-status my-brief M1 done\n"
            "  gaia milestone set-status my-brief M2 blocked --json\n"
        ),
    )
    setstatus_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    setstatus_p.add_argument("name", metavar="NAME", help="Milestone name.")
    setstatus_p.add_argument(
        "status",
        choices=("pending", "done", "blocked"),
        help="Target status.",
    )
    setstatus_p.add_argument("--workspace", default=None, metavar="W")
    setstatus_p.add_argument("--json", action="store_true", default=False,
                             help="Emit JSON.")

    # -- add -------------------------------------------------------------------
    add_p = actions.add_parser(
        "add",
        help="Add a milestone to a brief",
        description="Insert a new milestones row for a brief.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia milestone add my-brief M3 --description 'Ship v2'\n"
        ),
    )
    add_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    add_p.add_argument("name", metavar="NAME", help="Milestone name.")
    add_p.add_argument("--description", default=None, help="Milestone description.")
    add_p.add_argument("--order", type=int, default=None, metavar="N",
                       help="Explicit order_num. Auto-assigned if omitted.")
    add_p.add_argument("--workspace", default=None, metavar="W")
    add_p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON.")

    # -- remove ----------------------------------------------------------------
    remove_p = actions.add_parser(
        "remove",
        help="Remove a milestone from a brief",
        description="Delete a milestones row by name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia milestone remove my-brief M3\n"
        ),
    )
    remove_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    remove_p.add_argument("name", metavar="NAME", help="Milestone name to remove.")
    remove_p.add_argument("--workspace", default=None, metavar="W")
    remove_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON.")

    # -- edit ------------------------------------------------------------------
    edit_p = actions.add_parser(
        "edit",
        help="Edit an existing milestone",
        description=(
            "Update name or description of an existing milestone. "
            "Only specified fields are updated."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia milestone edit my-brief M1 --new-name 'Phase 1'\n"
            "  gaia milestone edit my-brief M1 --description 'Updated desc'\n"
        ),
    )
    edit_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    edit_p.add_argument("name", metavar="NAME", help="Current milestone name.")
    edit_p.add_argument("--new-name", dest="new_name", default=None,
                        help="New milestone name.")
    edit_p.add_argument("--description", default=None, help="New description.")
    edit_p.add_argument("--workspace", default=None, metavar="W")
    edit_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON.")


def cmd_milestone(args) -> int:
    """Dispatch handler for `gaia milestone`."""
    action = getattr(args, "milestone_action", None)
    handlers = {
        "set-status": _cmd_set_status,
        "add":        _cmd_add,
        "remove":     _cmd_remove,
        "edit":       _cmd_edit,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia milestone <set-status|add|remove|edit>", file=sys.stderr)
    return 0
