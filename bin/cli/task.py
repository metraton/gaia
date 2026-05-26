"""
gaia task -- Manage tasks (within plans) in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``.

Subcommands:
    gaia task set-status <brief> <task_id> <status>  Transition task status
                         [--workspace W] [--json]
    gaia task add <brief> --order=N --goal="..."      Add a task to a plan
                  [--workspace W] [--json]
    gaia task remove <brief> <order_num>              Remove a task from a plan
                     [--workspace W] [--json]
    gaia task reorder <brief> --from=A --to=B         Swap task order numbers
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
    from gaia.store.writer import set_task_status
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    task_id = args.task_id
    new_status = args.status
    as_json = getattr(args, "json", False)

    try:
        task_id_int = int(task_id)
    except (ValueError, TypeError):
        return _err(f"task_id must be an integer, got {task_id!r}", as_json=as_json)

    try:
        res = set_task_status(workspace, brief_name, task_id_int, new_status,
                              db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        if res.get("action") == "noop":
            print(f"Task {task_id} in '{brief_name}' already at status '{new_status}' (noop)")
        else:
            print(f"Task {task_id} in '{brief_name}': "
                  f"{res['old_status']} -> {res['new_status']}")
    return 0


def _cmd_add(args) -> int:
    from gaia.store.writer import add_task_to_plan
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    order_num = args.order
    goal = args.goal
    as_json = getattr(args, "json", False)

    try:
        res = add_task_to_plan(workspace, brief_name, order_num, goal, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Added task order_num={order_num} to plan for '{brief_name}'")
    return 0


def _cmd_remove(args) -> int:
    from gaia.store.writer import remove_task_from_plan
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    order_num = args.order_num
    as_json = getattr(args, "json", False)

    try:
        res = remove_task_from_plan(workspace, brief_name, order_num, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Removed task order_num={order_num} from plan for '{brief_name}'")
    return 0


def _cmd_reorder(args) -> int:
    from gaia.store.writer import reorder_tasks
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    from_num = args.from_num
    to_num = args.to_num
    as_json = getattr(args, "json", False)

    try:
        res = reorder_tasks(workspace, brief_name, [[from_num, to_num]], db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Reordered task {from_num} -> {to_num} in plan for '{brief_name}'")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `task` subcommand with the root parser."""
    task_parser = subparsers.add_parser(
        "task",
        help="Manage tasks within plans (DB-canonical)",
        description="Transition task status and manage task list within plans.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    task_parser.add_argument(
        "--workspace", metavar="W", default=None,
        help="Workspace identity. Default: gaia.project.current() or 'me'.",
    )

    actions = task_parser.add_subparsers(dest="task_action", metavar="<action>")

    # -- set-status ------------------------------------------------------------
    setstatus_p = actions.add_parser(
        "set-status",
        help="Transition a task's status",
        description="Validate and apply a task status transition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task set-status my-brief 1 done\n"
            "  gaia task set-status my-brief 2 skipped --json\n"
        ),
    )
    setstatus_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    setstatus_p.add_argument("task_id", metavar="TASK_ID",
                             help="Task order_num (integer).")
    setstatus_p.add_argument(
        "status",
        choices=("pending", "done", "skipped"),
        help="Target status.",
    )
    setstatus_p.add_argument("--workspace", default=None, metavar="W")
    setstatus_p.add_argument("--json", action="store_true", default=False,
                             help="Emit JSON.")

    # -- add -------------------------------------------------------------------
    add_p = actions.add_parser(
        "add",
        help="Add a task to a plan",
        description="Append a new task row to the plan attached to a brief.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task add my-brief --order=3 --goal='Implement feature X'\n"
        ),
    )
    add_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    add_p.add_argument("--order", type=int, required=True, metavar="N",
                       help="Order number for the new task.")
    add_p.add_argument("--goal", required=True, help="Task goal description.")
    add_p.add_argument("--workspace", default=None, metavar="W")
    add_p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON.")

    # -- remove ----------------------------------------------------------------
    remove_p = actions.add_parser(
        "remove",
        help="Remove a task from a plan",
        description="Delete a task row by order_num from the plan.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task remove my-brief 3\n"
        ),
    )
    remove_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    remove_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                          help="Task order_num to remove.")
    remove_p.add_argument("--workspace", default=None, metavar="W")
    remove_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON.")

    # -- reorder ---------------------------------------------------------------
    reorder_p = actions.add_parser(
        "reorder",
        help="Swap two task order numbers in a plan",
        description="Swap order_num A with order_num B for tasks in a plan.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task reorder my-brief --from=2 --to=4\n"
        ),
    )
    reorder_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    reorder_p.add_argument("--from", dest="from_num", type=int, required=True,
                           metavar="A", help="Source order_num.")
    reorder_p.add_argument("--to", dest="to_num", type=int, required=True,
                           metavar="B", help="Target order_num.")
    reorder_p.add_argument("--workspace", default=None, metavar="W")
    reorder_p.add_argument("--json", action="store_true", default=False,
                           help="Emit JSON.")


def cmd_task(args) -> int:
    """Dispatch handler for `gaia task`."""
    action = getattr(args, "task_action", None)
    handlers = {
        "set-status": _cmd_set_status,
        "add":        _cmd_add,
        "remove":     _cmd_remove,
        "reorder":    _cmd_reorder,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia task <set-status|add|remove|reorder>", file=sys.stderr)
    return 0
