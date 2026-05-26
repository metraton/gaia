"""
gaia ac -- Manage acceptance criteria for briefs in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``.

Subcommands:
    gaia ac set-status <brief> <ac_id> <status>  Transition AC status
                       [--workspace W] [--json]
    gaia ac add <brief> <ac_id>                  Add a new AC to a brief
                --description "..."
                [--evidence-type TYPE]
                [--evidence-shape JSON]
                [--artifact-path PATH]
                [--workspace W] [--json]
    gaia ac remove <brief> <ac_id>               Remove an AC from a brief
                   [--workspace W] [--json]
    gaia ac edit <brief> <ac_id>                 Edit an existing AC
                 [--description "..."]
                 [--evidence-type TYPE]
                 [--evidence-shape JSON]
                 [--artifact-path PATH]
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
    from gaia.store.writer import set_ac_status
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    ac_id = args.ac_id
    new_status = args.status
    as_json = getattr(args, "json", False)

    try:
        res = set_ac_status(workspace, brief_name, ac_id, new_status, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        if res.get("action") == "noop":
            print(f"AC '{ac_id}' in '{brief_name}' already at status '{new_status}' (noop)")
        else:
            print(f"AC '{ac_id}' in '{brief_name}': "
                  f"{res['old_status']} -> {res['new_status']}")
    return 0


def _cmd_add(args) -> int:
    from gaia.briefs.store import add_ac
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    ac_id = args.ac_id
    description = args.description
    evidence_type = getattr(args, "evidence_type", None)
    evidence_shape = getattr(args, "evidence_shape", None)
    artifact_path = getattr(args, "artifact_path", None)
    as_json = getattr(args, "json", False)

    try:
        res = add_ac(
            workspace, brief_name, ac_id,
            description=description,
            evidence_type=evidence_type,
            evidence_shape=evidence_shape,
            artifact_path=artifact_path,
            db_path=None,
        )
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Added AC '{ac_id}' to brief '{brief_name}'")
    return 0


def _cmd_remove(args) -> int:
    from gaia.briefs.store import remove_ac
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    ac_id = args.ac_id
    as_json = getattr(args, "json", False)

    try:
        res = remove_ac(workspace, brief_name, ac_id, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Removed AC '{ac_id}' from brief '{brief_name}'")
    return 0


def _cmd_edit(args) -> int:
    from gaia.briefs.store import update_ac
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    ac_id = args.ac_id
    description = getattr(args, "description", None)
    evidence_type = getattr(args, "evidence_type", None)
    evidence_shape = getattr(args, "evidence_shape", None)
    artifact_path = getattr(args, "artifact_path", None)
    as_json = getattr(args, "json", False)

    if all(v is None for v in [description, evidence_type, evidence_shape, artifact_path]):
        return _err(
            "At least one of --description, --evidence-type, --evidence-shape, "
            "--artifact-path is required for edit",
            as_json=as_json,
        )

    try:
        res = update_ac(
            workspace, brief_name, ac_id,
            description=description,
            evidence_type=evidence_type,
            evidence_shape=evidence_shape,
            artifact_path=artifact_path,
            db_path=None,
        )
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Updated AC '{ac_id}' in brief '{brief_name}'")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `ac` subcommand with the root parser."""
    ac_parser = subparsers.add_parser(
        "ac",
        help="Manage acceptance criteria for briefs (DB-canonical)",
        description=(
            "Transition AC status and add/remove/edit individual ACs "
            "without full-sync destruction."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ac_parser.add_argument(
        "--workspace", metavar="W", default=None,
        help="Workspace identity. Default: gaia.project.current() or 'me'.",
    )

    actions = ac_parser.add_subparsers(dest="ac_action", metavar="<action>")

    # -- set-status ------------------------------------------------------------
    setstatus_p = actions.add_parser(
        "set-status",
        help="Transition an AC's status",
        description="Validate and apply an AC status transition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia ac set-status my-brief AC-1 done\n"
            "  gaia ac set-status my-brief AC-2 blocked --json\n"
        ),
    )
    setstatus_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    setstatus_p.add_argument("ac_id", metavar="AC_ID", help="AC identifier.")
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
        help="Add a new AC to a brief",
        description="Insert a new acceptance_criteria row for a brief.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia ac add my-brief AC-3 --description 'Tests pass'\n"
        ),
    )
    add_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    add_p.add_argument("ac_id", metavar="AC_ID", help="AC identifier (e.g. AC-3).")
    add_p.add_argument("--description", default=None, help="AC description text.")
    add_p.add_argument("--evidence-type", dest="evidence_type", default=None,
                       help="Evidence type (e.g. 'test', 'metric', 'review').")
    add_p.add_argument("--evidence-shape", dest="evidence_shape", default=None,
                       help="Evidence shape as JSON string.")
    add_p.add_argument("--artifact-path", dest="artifact_path", default=None,
                       help="Path to artifact file.")
    add_p.add_argument("--workspace", default=None, metavar="W")
    add_p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON.")

    # -- remove ----------------------------------------------------------------
    remove_p = actions.add_parser(
        "remove",
        help="Remove an AC from a brief",
        description="Delete an acceptance_criteria row by ac_id.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia ac remove my-brief AC-3\n"
        ),
    )
    remove_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    remove_p.add_argument("ac_id", metavar="AC_ID", help="AC identifier to remove.")
    remove_p.add_argument("--workspace", default=None, metavar="W")
    remove_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON.")

    # -- edit ------------------------------------------------------------------
    edit_p = actions.add_parser(
        "edit",
        help="Edit an existing AC",
        description=(
            "Update fields of an existing AC. Only specified fields are updated; "
            "omitted fields are preserved."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia ac edit my-brief AC-1 --description 'Updated desc'\n"
            "  gaia ac edit my-brief AC-1 --artifact-path /tmp/report.html\n"
        ),
    )
    edit_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    edit_p.add_argument("ac_id", metavar="AC_ID", help="AC identifier to edit.")
    edit_p.add_argument("--description", default=None, help="New description text.")
    edit_p.add_argument("--evidence-type", dest="evidence_type", default=None,
                        help="New evidence type.")
    edit_p.add_argument("--evidence-shape", dest="evidence_shape", default=None,
                        help="New evidence shape as JSON string.")
    edit_p.add_argument("--artifact-path", dest="artifact_path", default=None,
                        help="New artifact path.")
    edit_p.add_argument("--workspace", default=None, metavar="W")
    edit_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON.")


def cmd_ac(args) -> int:
    """Dispatch handler for `gaia ac`."""
    action = getattr(args, "ac_action", None)
    handlers = {
        "set-status": _cmd_set_status,
        "add":        _cmd_add,
        "remove":     _cmd_remove,
        "edit":       _cmd_edit,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia ac <set-status|add|remove|edit>", file=sys.stderr)
    return 0
