"""
gaia ac -- Manage acceptance criteria for briefs in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``.

Subcommands:
    gaia ac set-status <brief> <ac_id> <status>  Transition AC status
                       [--workspace W] [--json]
    gaia ac add <brief> <ac_id>                  Add a new AC to a brief
                [--description "..." | --description-file PATH]
                [--evidence-type TYPE]
                [--evidence-shape JSON | --evidence-shape-file PATH]
                [--artifact-path PATH]
                [--workspace W] [--json]
    gaia ac remove <brief> <ac_id>               Remove an AC from a brief
                   [--workspace W] [--json]
    gaia ac edit <brief> <ac_id>                 Edit an existing AC in place
                 (preserves ac_id and list position; wraps
                 gaia.briefs.store.update_ac)
                 [--description "..." | --description-file PATH]
                 [--evidence-type TYPE]
                 [--evidence-shape JSON | --evidence-shape-file PATH]
                 [--artifact-path PATH]
                 [--workspace W] [--json]

``--description-file`` / ``--evidence-shape-file`` (mutex with their inline
counterparts) read the value from a file, or from stdin with ``-``. Use these
for evidence_shape prose containing ``<placeholder>`` tokens or ``; expect
...`` clauses -- that text can otherwise trip the command pre-execution
security scan when inlined as a shell argument. Same convention as `gaia
brief edit --content-file` / `gaia brief new --objective-file`.
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


def _read_content_file(path_str: str) -> str:
    """Read content text from a file path or stdin.

    Pass ``"-"`` to read from ``sys.stdin`` until EOF (utf-8). Pass any other
    path string to read that file (utf-8). Raises ``FileNotFoundError`` for
    missing paths (caller converts to _err). Mirrors bin/cli/brief.py's helper
    of the same name -- the established convention for long text that must
    not be inlined as a shell argument.
    """
    if path_str == "-":
        return sys.stdin.read()
    return Path(path_str).read_text(encoding="utf-8")


def _resolve_file_arg(inline_val, file_val, flag_name):
    """Return the effective value for a --X / --X-file mutex pair.

    Raises ValueError (caller converts to _err) on a missing/unreadable file.
    ``file_val`` of None means --X-file was not given; inline_val passes
    through unchanged in that case (including None, meaning neither was given).
    """
    if file_val is None:
        return inline_val
    try:
        return _read_content_file(file_val)
    except FileNotFoundError:
        raise ValueError(f"--{flag_name}-file: file not found: {file_val}")
    except OSError as exc:
        raise ValueError(f"--{flag_name}-file: cannot read '{file_val}': {exc}")


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
    evidence_type = getattr(args, "evidence_type", None)
    artifact_path = getattr(args, "artifact_path", None)
    as_json = getattr(args, "json", False)

    try:
        description = _resolve_file_arg(
            args.description, getattr(args, "description_file", None), "description"
        )
        evidence_shape = _resolve_file_arg(
            getattr(args, "evidence_shape", None),
            getattr(args, "evidence_shape_file", None),
            "evidence-shape",
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    try:
        if artifact_path is not None:
            from gaia.evidence.fs import require_canonical_artifact_path
            artifact_path = require_canonical_artifact_path(artifact_path)
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
    evidence_type = getattr(args, "evidence_type", None)
    artifact_path = getattr(args, "artifact_path", None)
    as_json = getattr(args, "json", False)

    try:
        description = _resolve_file_arg(
            getattr(args, "description", None),
            getattr(args, "description_file", None),
            "description",
        )
        evidence_shape = _resolve_file_arg(
            getattr(args, "evidence_shape", None),
            getattr(args, "evidence_shape_file", None),
            "evidence-shape",
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if all(v is None for v in [description, evidence_type, evidence_shape, artifact_path]):
        return _err(
            "At least one of --description/--description-file, --evidence-type, "
            "--evidence-shape/--evidence-shape-file, --artifact-path is "
            "required for edit",
            as_json=as_json,
        )

    try:
        if artifact_path is not None:
            from gaia.evidence.fs import require_canonical_artifact_path
            artifact_path = require_canonical_artifact_path(artifact_path)
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
        choices=("pending", "done", "blocked", "descoped"),
        help="Target status ('descoped' is a hard-terminal drop; no reopen).",
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
    _add_desc_group = add_p.add_mutually_exclusive_group()
    _add_desc_group.add_argument("--description", default=None,
                                 help="AC description text.")
    _add_desc_group.add_argument(
        "--description-file", dest="description_file", default=None, metavar="PATH",
        help="Read --description from PATH. Use '-' to read from stdin.",
    )
    add_p.add_argument("--evidence-type", dest="evidence_type", default=None,
                       help="Evidence type (e.g. 'test', 'metric', 'review').")
    _add_shape_group = add_p.add_mutually_exclusive_group()
    _add_shape_group.add_argument("--evidence-shape", dest="evidence_shape",
                                  default=None,
                                  help="Evidence shape as JSON string.")
    _add_shape_group.add_argument(
        "--evidence-shape-file", dest="evidence_shape_file", default=None,
        metavar="PATH",
        help=(
            "Read --evidence-shape from PATH. Use '-' to read from stdin. "
            "Recommended when the shape prose contains '<placeholder>' "
            "tokens or '; expect ...' clauses -- inlining that text as a "
            "shell argument can trip the command pre-execution security scan."
        ),
    )
    add_p.add_argument("--artifact-path", dest="artifact_path", default=None,
                       help=("Existing absolute blob path returned by `gaia "
                             "evidence add`; relative paths are rejected."))
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
        help="Edit an existing AC in place (preserves id and list position)",
        description=(
            "Update fields of an existing AC IN PLACE via an UPDATE ... WHERE "
            "id = ? against the existing row -- the AC's id and its position "
            "in the list are both preserved (wraps "
            "gaia.briefs.store.update_ac). Only specified fields are updated; "
            "omitted fields are preserved. This is the correct way to correct "
            "an existing AC; 'remove' + 'add' instead deletes the row and "
            "inserts a fresh one with a new id at the END of the list."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia ac edit my-brief AC-1 --description 'Updated desc'\n"
            "  gaia ac edit my-brief AC-1 --artifact-path /tmp/report.html\n"
            "  gaia ac edit my-brief AC-1 "
            "--evidence-shape-file /tmp/ac1-shape.txt\n"
        ),
    )
    edit_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    edit_p.add_argument("ac_id", metavar="AC_ID", help="AC identifier to edit.")
    _edit_desc_group = edit_p.add_mutually_exclusive_group()
    _edit_desc_group.add_argument("--description", default=None,
                                  help="New description text.")
    _edit_desc_group.add_argument(
        "--description-file", dest="description_file", default=None, metavar="PATH",
        help="Read --description from PATH. Use '-' to read from stdin.",
    )
    edit_p.add_argument("--evidence-type", dest="evidence_type", default=None,
                        help="New evidence type.")
    _edit_shape_group = edit_p.add_mutually_exclusive_group()
    _edit_shape_group.add_argument("--evidence-shape", dest="evidence_shape",
                                   default=None,
                                   help="New evidence shape as JSON string.")
    _edit_shape_group.add_argument(
        "--evidence-shape-file", dest="evidence_shape_file", default=None,
        metavar="PATH",
        help=(
            "Read --evidence-shape from PATH. Use '-' to read from stdin. "
            "Recommended when the shape prose contains '<placeholder>' "
            "tokens or '; expect ...' clauses -- inlining that text as a "
            "shell argument can trip the command pre-execution security scan."
        ),
    )
    edit_p.add_argument("--artifact-path", dest="artifact_path", default=None,
                        help=("New canonical blob path returned by `gaia "
                              "evidence add`; relative paths are rejected."))
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
