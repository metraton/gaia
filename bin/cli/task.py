"""
gaia task -- Manage tasks (within plans) in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``.

Subcommands:
    gaia task set-status <brief> <task_id> <status>  Transition task status
                         [--workspace W] [--json]
    gaia task add <brief> --order=N --goal="..."      Add a task to a plan
                  [--workspace W] [--json]
    gaia task list <brief>                            List a plan's tasks (read-only)
                   [--status=pending|done|skipped]
                   [--format=table|json|count] [--workspace W]
    gaia task remove <brief> <order_num>              Remove a task from a plan
                     [--workspace W] [--json]
    gaia task reorder <brief> --from=A --to=B         Swap task order numbers
                      [--workspace W] [--json]
    gaia task gate add <brief> <order_num> --type=T   Add a verification gate
                       [--evidence-type] [--evidence-shape] [--artifact-path]
                       [--status] [--workspace W] [--json]
    gaia task gate list <brief> <order_num>           List a task's gates
                        [--workspace W] [--json]
    gaia task gate remove <brief> <order_num> <gate_id>  Remove a gate
                          [--workspace W] [--json]
    gaia task gate set-status <brief> <order_num> <gate_id> <status>
                          Set a gate's status (pending|pass|fail)
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


def _cmd_list(args) -> int:
    """Read-only: list the tasks of the ONE plan attached to a brief.

    Mirrors `gaia brief list` (bin/cli/brief.py `_cmd_list`): a `--status`
    filter and a `--format=table|json|count` selector where `count` prints
    only the number -- the cheap answer to "how many tasks / how many
    pending". Scoped to a single plan (plans.brief_id is UNIQUE), never the
    whole workspace.
    """
    from gaia.store.writer import list_plan_tasks

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    status = getattr(args, "status", None)
    fmt = getattr(args, "format", None) or "table"
    as_json = fmt == "json"

    try:
        tasks = list_plan_tasks(workspace, brief_name, status=status,
                                db_path=None)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if fmt == "count":
        print(len(tasks))
        return 0
    if fmt == "json":
        print(json.dumps(tasks, indent=2, default=str))
        return 0

    # table
    if not tasks:
        print("(no tasks)")
        return 0
    order_w = max(5, max(len(str(t["order_num"])) for t in tasks))
    status_w = max(6, max(len((t["status"] or "")) for t in tasks))
    goal_w = max(4, max(len((t.get("goal") or "")) for t in tasks))
    print(f"{'ORDER':<{order_w}}  {'STATUS':<{status_w}}  {'GOAL':<{goal_w}}")
    print("-" * (order_w + status_w + goal_w + 4))
    for t in tasks:
        print(f"{str(t['order_num']):<{order_w}}  "
              f"{(t['status'] or ''):<{status_w}}  "
              f"{(t.get('goal') or ''):<{goal_w}}")
    return 0


# ---------------------------------------------------------------------------
# gate sub-action handlers (gaia task gate add|list|remove)
#
# A gate is addressed by its parent task's order_num within a brief's plan --
# consistent with how `gaia task add/remove` address tasks. The CLI persists
# the gate AS GIVEN; the pure structural validator lives separately in
# gaia.state.gate_validation and is not invoked at write time (R1-A scope).
# ---------------------------------------------------------------------------

def _cmd_gate_add(args) -> int:
    from gaia.store.writer import add_gate_to_task
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)

    try:
        res = add_gate_to_task(
            workspace, args.brief, args.order_num, args.type,
            evidence_type=args.evidence_type,
            evidence_shape=args.evidence_shape,
            artifact_path=args.artifact_path,
            status=args.status,
            db_path=None,
        )
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Added gate id={res['gate_id']} (type={res['verification_type']}) "
              f"to task order_num={args.order_num} in '{args.brief}'")
    return 0


def _cmd_gate_list(args) -> int:
    from gaia.store.writer import list_task_gates

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)

    try:
        gates = list_task_gates(workspace, args.brief, args.order_num,
                                db_path=None)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(gates, indent=2, default=str))
    else:
        if not gates:
            print(f"No gates on task order_num={args.order_num} in '{args.brief}'")
        else:
            for g in gates:
                print(f"  gate id={g['id']} type={g['verification_type']} "
                      f"status={g['status']} evidence_type={g['evidence_type']}")
    return 0


def _cmd_gate_remove(args) -> int:
    from gaia.store.writer import remove_gate_from_task
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)

    try:
        res = remove_gate_from_task(workspace, args.brief, args.order_num,
                                    args.gate_id, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Removed gate id={args.gate_id} from task order_num={args.order_num} "
              f"in '{args.brief}'")
    return 0


def _cmd_gate_set_status(args) -> int:
    from gaia.store.writer import set_gate_status
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)

    try:
        res = set_gate_status(workspace, args.brief, args.order_num,
                              args.gate_id, args.status, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Gate id={args.gate_id} on task order_num={args.order_num} "
              f"in '{args.brief}': {res['old_status']} -> {res['new_status']}")
    return 0


def _cmd_gate(args) -> int:
    """Dispatch handler for `gaia task gate`."""
    action = getattr(args, "gate_action", None)
    handlers = {
        "add":        _cmd_gate_add,
        "list":       _cmd_gate_list,
        "remove":     _cmd_gate_remove,
        "set-status": _cmd_gate_set_status,
    }
    if action in handlers:
        return handlers[action](args)
    print("Usage: gaia task gate <add|list|remove|set-status>", file=sys.stderr)
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

    # -- list ------------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List the tasks of the plan attached to a brief (read-only)",
        description=(
            "List the task rows of the ONE plan attached to a brief, scoped to "
            "that plan. Mirrors `gaia brief list`: --status filter and "
            "--format=table|json|count (count prints only the number)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task list my-brief\n"
            "  gaia task list my-brief --status=pending --format=count\n"
            "  gaia task list my-brief --format=json\n"
        ),
    )
    list_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    list_p.add_argument("--status", default=None,
                        choices=("pending", "done", "skipped"),
                        help="Filter by task status.")
    list_p.add_argument("--format", default="table",
                        choices=("table", "json", "count"),
                        help="Output shape. Default: table.")
    list_p.add_argument("--workspace", default=None, metavar="W")

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

    # -- gate (add|list|remove) ------------------------------------------------
    gate_p = actions.add_parser(
        "gate",
        help="Add / list / remove a verification gate on a task",
        description="Manage planner-authored typed verification gates on a task.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task gate add my-brief 1 --type=command "
            "--evidence-shape='pytest -q'\n"
            "  gaia task gate list my-brief 1\n"
            "  gaia task gate remove my-brief 1 3\n"
            "  gaia task gate set-status my-brief 1 3 pass\n"
        ),
    )
    gate_actions = gate_p.add_subparsers(dest="gate_action", metavar="<action>")

    gate_add_p = gate_actions.add_parser(
        "add", help="Add a verification gate to a task",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gate_add_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    gate_add_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                            help="Parent task order_num.")
    gate_add_p.add_argument(
        "--type", dest="type", required=True,
        choices=("command", "code", "semantic", "self_review"),
        help="Verification type (VALID_VERIFICATION_TYPES).",
    )
    gate_add_p.add_argument("--evidence-type", dest="evidence_type",
                            default=None, help="Evidence type descriptor.")
    gate_add_p.add_argument("--evidence-shape", dest="evidence_shape",
                            default=None, help="Evidence shape / check spec.")
    gate_add_p.add_argument("--artifact-path", dest="artifact_path",
                            default=None, help="Artifact path for evidence.")
    gate_add_p.add_argument("--status", default="pending",
                            choices=("pending", "pass", "fail"),
                            help="Gate status (VALID_GATE_STATUSES; default 'pending').")
    gate_add_p.add_argument("--workspace", default=None, metavar="W")
    gate_add_p.add_argument("--json", action="store_true", default=False,
                            help="Emit JSON.")

    gate_list_p = gate_actions.add_parser(
        "list", help="List a task's gates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gate_list_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    gate_list_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                             help="Parent task order_num.")
    gate_list_p.add_argument("--workspace", default=None, metavar="W")
    gate_list_p.add_argument("--json", action="store_true", default=False,
                             help="Emit JSON.")

    gate_remove_p = gate_actions.add_parser(
        "remove", help="Remove a gate from a task",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    gate_remove_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    gate_remove_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                               help="Parent task order_num.")
    gate_remove_p.add_argument("gate_id", type=int, metavar="GATE_ID",
                               help="task_gates.id to remove.")
    gate_remove_p.add_argument("--workspace", default=None, metavar="W")
    gate_remove_p.add_argument("--json", action="store_true", default=False,
                               help="Emit JSON.")

    gate_setstatus_p = gate_actions.add_parser(
        "set-status", help="Set a gate's status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task gate set-status my-brief 1 3 pass\n"
        ),
    )
    gate_setstatus_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    gate_setstatus_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                                  help="Parent task order_num.")
    gate_setstatus_p.add_argument("gate_id", type=int, metavar="GATE_ID",
                                  help="task_gates.id to update.")
    gate_setstatus_p.add_argument(
        "status",
        choices=("pending", "pass", "fail"),
        help="Target gate status (VALID_GATE_STATUSES).",
    )
    gate_setstatus_p.add_argument("--workspace", default=None, metavar="W")
    gate_setstatus_p.add_argument("--json", action="store_true", default=False,
                                  help="Emit JSON.")


def cmd_task(args) -> int:
    """Dispatch handler for `gaia task`."""
    action = getattr(args, "task_action", None)
    handlers = {
        "set-status": _cmd_set_status,
        "add":        _cmd_add,
        "list":       _cmd_list,
        "remove":     _cmd_remove,
        "reorder":    _cmd_reorder,
        "gate":       _cmd_gate,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia task <set-status|add|list|remove|reorder|gate>",
          file=sys.stderr)
    return 0
