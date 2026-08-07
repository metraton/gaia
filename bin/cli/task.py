"""
gaia task -- Manage tasks (within plans) in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``.

Subcommands:
    gaia task set-status <brief> <task_id> <status>  Transition task status
                         [--override --reason="..."] [--workspace W] [--json]
    gaia task add <brief> --order=N --goal="..."      Add a task to a plan
                  [--workspace W] [--json]
    gaia task list <brief>                            List a plan's tasks (read-only)
                   [--status=pending|done|skipped]
                   [--format=table|json|count] [--json] [--workspace W]
    gaia task show <brief> <order_num>                Show one task (read-only)
                   Prints ORDER_NUM (plan position) and TASK_ID (tasks.id --
                   the value the dispatch contract's task_id=<N> token
                   requires) clearly labeled and never conflated.
                   [--json] [--workspace W]
    gaia task remove <brief> <order_num>              Remove a task from a plan
                     [--workspace W] [--json]
    gaia task edit <brief> <order_num> --goal="..."   Edit a task's goal IN PLACE
                   [--goal-file]                      (preserves task id AND
                                                        its task_gates -- unlike
                                                        remove + add, which
                                                        cascades away every gate)
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
    gaia task gate edit <brief> <order_num> <gate_id> Edit a gate IN PLACE
                        [--verification-type] [--evidence-type]           (only
                        [--evidence-shape|--evidence-shape-file]           the
                        [--artifact-path]                                  given
                                                                            fields
                                                                            change)
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

def _read_content_file(path_str: str) -> str:
    """Read content text from a file path or stdin.

    Pass ``"-"`` to read from ``sys.stdin`` until EOF (utf-8). Pass any other
    path string to read that file (utf-8). Raises ``FileNotFoundError`` for
    missing paths (caller converts to _err). Mirrors ``cli.brief``'s helper
    of the same name.
    """
    if path_str == "-":
        return sys.stdin.read()
    return Path(path_str).read_text(encoding="utf-8")


def _resolve_field(inline_val, file_val, field_name):
    """Return the effective value for a --X / --X-file mutex pair.

    ``file_val`` of None means --X-file was not given; ``inline_val`` passes
    through unchanged in that case (including None, meaning neither was
    given). Raises ValueError (caller converts to _err) on a missing/unreadable
    file. Mirrors ``cli.brief``'s helper of the same name -- long-text fields
    (a goal, an evidence shape) sharing that convention across CLI files.
    """
    if file_val is None:
        return inline_val
    try:
        return _read_content_file(file_val)
    except FileNotFoundError:
        raise ValueError(f"--{field_name}-file: file not found: {file_val}")
    except OSError as exc:
        raise ValueError(f"--{field_name}-file: cannot read '{file_val}': {exc}")


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

# The two override flags are required to travel together, and each half of that
# pairing is refused for its own reason. `--override` without a reason would be
# an unaccountable close, which the channel exists to prevent. `--reason`
# without `--override` is the more insidious half: the writer's API carries a
# single ``override_reason``, so a reason with no flag to arm it would be
# dropped, leaving the operator believing they had recorded a justification.
_REASON_WITHOUT_OVERRIDE_MESSAGE = (
    "--reason states why a task is being closed against its gates, which is an "
    "override: pass --override alongside it. Without that flag the reason would "
    "not be recorded anywhere."
)


def _cmd_set_status(args) -> int:
    from gaia.store.writer import set_task_status
    from gaia.state.permissions import StateTransitionForbidden
    from gaia.state.task_closure_event import normalize_reason

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    task_id = args.task_id
    new_status = args.status
    as_json = getattr(args, "json", False)
    override = bool(getattr(args, "override", False))
    reason = getattr(args, "reason", None)

    try:
        task_id_int = int(task_id)
    except (ValueError, TypeError):
        return _err(f"task_id must be an integer, got {task_id!r}", as_json=as_json)

    if override:
        # The one validator, reused: `normalize_reason` owns what counts as a
        # stated reason, so `--override` with nothing behind it fails here with
        # the same message a direct caller of the writer would see.
        try:
            normalize_reason(reason)
        except ValueError as exc:
            return _err(str(exc), as_json=as_json)
    elif reason is not None:
        return _err(_REASON_WITHOUT_OVERRIDE_MESSAGE, as_json=as_json)

    try:
        res = set_task_status(workspace, brief_name, task_id_int, new_status,
                              override_reason=reason if override else None,
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


def _cmd_edit(args) -> int:
    """Edit a task's goal IN PLACE (wraps gaia.store.writer.update_task).

    Preserves the task's id and, critically, every task_gates row attached to
    it -- 'remove' + 'add' deletes the task row and, through the ON DELETE
    CASCADE from task_gates.task_id, destroys every gate along with it. This
    is the verb to reach for whenever a task's scope needs adjusting and its
    gates must survive the edit.
    """
    from gaia.store.writer import update_task
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    order_num = args.order_num
    as_json = getattr(args, "json", False)

    try:
        goal = _resolve_field(
            getattr(args, "goal", None), getattr(args, "goal_file", None), "goal",
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if goal is None:
        return _err(
            "--goal or --goal-file is required for edit", as_json=as_json,
        )

    try:
        res = update_task(workspace, brief_name, order_num, goal=goal, db_path=None)
    except StateTransitionForbidden as exc:
        return _err(f"forbidden: {exc}", as_json=as_json)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Edited task order_num={order_num} in '{brief_name}': goal updated")
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

    The table view prints both ORDER (the plan-position ordinal) and TASK_ID
    (``tasks.id`` -- the row id the dispatch contract's ``task_id=<N>`` token
    requires) as separate, clearly labeled columns: the whole failure mode
    this closes is confusing the two, so they are never merged into one
    ambiguous number.
    """
    from gaia.store.writer import list_plan_tasks

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    status = getattr(args, "status", None)
    fmt = getattr(args, "format", None) or "table"
    as_json = getattr(args, "json", False) or fmt == "json"
    if as_json:
        fmt = "json"

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
    id_w = max(7, max(len(str(t["id"])) for t in tasks))
    status_w = max(6, max(len((t["status"] or "")) for t in tasks))
    goal_w = max(4, max(len((t.get("goal") or "")) for t in tasks))
    print(f"{'ORDER':<{order_w}}  {'TASK_ID':<{id_w}}  "
          f"{'STATUS':<{status_w}}  {'GOAL':<{goal_w}}")
    print("-" * (order_w + id_w + status_w + goal_w + 6))
    for t in tasks:
        print(f"{str(t['order_num']):<{order_w}}  "
              f"{str(t['id']):<{id_w}}  "
              f"{(t['status'] or ''):<{status_w}}  "
              f"{(t.get('goal') or ''):<{goal_w}}")
    return 0


def _cmd_show(args) -> int:
    """Read-only: show a single task of the ONE plan attached to a brief.

    Addressed by ``order_num`` -- consistent with every other single-task
    verb in this file (`remove`, `gate add/list/remove/set-status`). Prints
    ORDER_NUM (the plan-position ordinal a human reads/types) and TASK_ID
    (``tasks.id``, the row id the dispatch contract's ``task_id=<N>`` token
    requires) as two separate, explicitly labeled lines -- the two numbers
    are never the same value, and conflating them is the exact failure mode
    this verb exists to close.
    """
    from gaia.store.writer import get_task_by_order

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief
    order_num = args.order_num
    as_json = getattr(args, "json", False)

    try:
        task = get_task_by_order(workspace, brief_name, order_num, db_path=None)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if task is None:
        return _err(
            f"no task at order_num={order_num} in the plan for '{brief_name}'",
            as_json=as_json,
        )

    if as_json:
        print(json.dumps(task, indent=2, default=str))
        return 0

    print(f"BRIEF:      {brief_name}")
    print(f"ORDER_NUM:  {task['order_num']}   "
          f"(plan position -- NOT the dispatch id)")
    print(f"TASK_ID:    {task['id']}   "
          f"(tasks.id -- pass this as task_id={task['id']} to dispatch)")
    print(f"STATUS:     {task['status']}")
    print(f"GOAL:       {task.get('goal') or ''}")
    print(f"EVIDENCE:   {task.get('evidence_path') or '(none)'}")
    return 0


# ---------------------------------------------------------------------------
# gate sub-action handlers (gaia task gate add|list|remove|set-status|edit)
#
# A gate is addressed by its parent task's order_num within a brief's plan --
# consistent with how `gaia task add/remove/edit` address tasks. The CLI
# persists the gate AS GIVEN, on both `add` and `edit`; the pure structural
# validator lives separately in gaia.state.gate_validation and is not invoked
# at write time (R1-A scope).
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
        _print_derived_closure(res, args)
    return 0


def _print_derived_closure(res: dict, args) -> None:
    """Report what the recorded verdict implied for the task itself.

    The operator asked to record a gate verdict and may get a task transition
    they did not type; an unannounced status change is the one outcome of the
    automatism that would read as the substrate moving on its own. The inaction
    branch is reported too, but only its reason -- silence there would leave
    "why did this verdict not close the task?" answerable only by re-deriving it
    by hand.

    A failed derivation is printed on stderr and does NOT change the exit code:
    the verdict the operator asked for IS recorded, and a non-zero exit would
    invite them to re-issue a write that already landed.
    """
    from gaia.store.writer import (
        DERIVED_CLOSURE_ERROR_ACTION,
        DERIVED_CLOSURE_RESULT_KEY,
    )

    derived = res.get(DERIVED_CLOSURE_RESULT_KEY) or {}
    action = derived.get("action")
    task = f"task order_num={args.order_num} in '{args.brief}'"

    if action == DERIVED_CLOSURE_ERROR_ACTION:
        print(
            f"  WARNING: the gate verdict IS recorded, but the derived "
            f"{derived.get('intended_action')} of {task} failed: "
            f"{derived.get('error')}",
            file=sys.stderr,
        )
    elif action in ("close", "reopen"):
        print(f"  Derived {action}: {task} "
              f"{derived.get('old_status')} -> {derived.get('new_status')} "
              f"-- {derived.get('why')}")
    elif derived.get("why"):
        print(f"  No derived transition: {derived['why']}")


def _cmd_gate_edit(args) -> int:
    """Edit a gate's content fields IN PLACE (wraps gaia.store.writer.update_gate).

    Partial update: only the flags given change; the rest of the row, and its
    id, are untouched. Never touches .status -- that transition stays the job
    of `gate set-status` alone. Mirrors `gaia brief ac edit`'s in-place
    convention: prefer this over 'remove' + 'add', which deletes the row and
    inserts a fresh one with a new id.
    """
    from gaia.store.writer import update_gate
    from gaia.state.permissions import StateTransitionForbidden

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    as_json = getattr(args, "json", False)

    try:
        evidence_shape = _resolve_field(
            getattr(args, "evidence_shape", None),
            getattr(args, "evidence_shape_file", None), "evidence-shape",
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    verification_type = getattr(args, "verification_type", None)
    evidence_type = getattr(args, "evidence_type", None)
    artifact_path = getattr(args, "artifact_path", None)

    if all(v is None for v in
           [verification_type, evidence_type, evidence_shape, artifact_path]):
        return _err(
            "at least one of --verification-type, --evidence-type, "
            "--evidence-shape/--evidence-shape-file, --artifact-path "
            "is required for edit",
            as_json=as_json,
        )

    try:
        res = update_gate(
            workspace, args.brief, args.order_num, args.gate_id,
            verification_type=verification_type,
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
        print(f"Edited gate id={args.gate_id} on task order_num={args.order_num} "
              f"in '{args.brief}': {', '.join(res['fields'])} updated")
    return 0


def _cmd_gate(args) -> int:
    """Dispatch handler for `gaia task gate`."""
    action = getattr(args, "gate_action", None)
    handlers = {
        "add":        _cmd_gate_add,
        "list":       _cmd_gate_list,
        "remove":     _cmd_gate_remove,
        "set-status": _cmd_gate_set_status,
        "edit":       _cmd_gate_edit,
    }
    if action in handlers:
        return handlers[action](args)
    print("Usage: gaia task gate <add|list|remove|set-status|edit>", file=sys.stderr)
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
        description=(
            "Validate and apply a task status transition. Closing a task "
            "('done') requires either an approving gate verdict or an explicit "
            "--override with a --reason, which is recorded as an auditable "
            "event. 'skipped' and reopening to 'pending' carry no gate "
            "condition."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task set-status my-brief 1 done\n"
            "  gaia task set-status my-brief 1 done --override "
            "--reason='the gate runner is offline'\n"
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
    setstatus_p.add_argument(
        "--override", action="store_true", default=False,
        help=("Close the task despite gates that have not passed. Requires "
              "--reason and leaves an auditable event."),
    )
    setstatus_p.add_argument(
        "--reason", default=None, metavar="TEXT",
        help="Why the task is being closed against its gates (needs --override).",
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
    list_p.add_argument("--json", action="store_true", default=False,
                        help="Alias for --format=json.")
    list_p.add_argument("--workspace", default=None, metavar="W")

    # -- show ------------------------------------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Show one task of the plan attached to a brief (read-only)",
        description=(
            "Show a single task, addressed by order_num (consistent with "
            "`remove`/`gate`). Prints ORDER_NUM (the plan-position ordinal) "
            "and TASK_ID (tasks.id -- the row id the dispatch contract's "
            "task_id=<N> token requires) as two separate, explicitly "
            "labeled values -- never conflate the two."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task show my-brief 1\n"
            "  gaia task show my-brief 1 --json\n"
        ),
    )
    show_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    show_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                        help="Task order_num to show.")
    show_p.add_argument("--workspace", default=None, metavar="W")
    show_p.add_argument("--json", action="store_true", default=False,
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

    # -- edit --------------------------------------------------------------------
    edit_p = actions.add_parser(
        "edit",
        help="Edit a task's goal in place",
        description=(
            "Update a task's goal IN PLACE (wraps gaia.store.writer.update_task) "
            "-- the task's id and its task_gates rows are both preserved. Prefer "
            "this over 'remove' + 'add', which deletes the task row and, through "
            "the ON DELETE CASCADE from task_gates.task_id, destroys every gate "
            "attached to it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task edit my-brief 3 --goal='Implement feature X, revised'\n"
            "  gaia task edit my-brief 3 --goal-file=/tmp/goal.txt\n"
        ),
    )
    edit_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    edit_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                        help="Task order_num to edit.")
    _edit_goal_group = edit_p.add_mutually_exclusive_group()
    _edit_goal_group.add_argument("--goal", default=None, help="New task goal.")
    _edit_goal_group.add_argument(
        "--goal-file", dest="goal_file", default=None, metavar="PATH",
        help="Read --goal from PATH. Use '-' to read from stdin.",
    )
    edit_p.add_argument("--workspace", default=None, metavar="W")
    edit_p.add_argument("--json", action="store_true", default=False,
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

    # -- gate (add|list|remove|set-status|edit) --------------------------------
    gate_p = actions.add_parser(
        "gate",
        help="Add / list / remove / edit a verification gate on a task",
        description="Manage planner-authored typed verification gates on a task.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task gate add my-brief 1 --type=command "
            "--evidence-shape='pytest -q'\n"
            "  gaia task gate list my-brief 1\n"
            "  gaia task gate remove my-brief 1 3\n"
            "  gaia task gate set-status my-brief 1 3 pass\n"
            "  gaia task gate edit my-brief 1 3 --evidence-shape='pytest -q -k foo'\n"
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

    gate_edit_p = gate_actions.add_parser(
        "edit",
        help="Edit a gate's content fields in place",
        description=(
            "Update fields of an existing gate IN PLACE (wraps "
            "gaia.store.writer.update_gate) -- the gate's id is preserved and "
            "only the given fields change. Never touches .status; use "
            "'set-status' for that. Prefer this over 'remove' + 'add', which "
            "deletes the row and inserts a fresh one with a new id."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia task gate edit my-brief 1 3 --evidence-shape='pytest -q -k foo'\n"
            "  gaia task gate edit my-brief 1 3 "
            "--evidence-shape-file=/tmp/gate3-shape.txt\n"
        ),
    )
    gate_edit_p.add_argument("brief", metavar="BRIEF", help="Parent brief slug.")
    gate_edit_p.add_argument("order_num", type=int, metavar="ORDER_NUM",
                             help="Parent task order_num.")
    gate_edit_p.add_argument("gate_id", type=int, metavar="GATE_ID",
                             help="task_gates.id to edit.")
    gate_edit_p.add_argument(
        "--verification-type", dest="verification_type", default=None,
        choices=("command", "code", "semantic", "self_review"),
        help="New verification type (VALID_VERIFICATION_TYPES).",
    )
    gate_edit_p.add_argument("--evidence-type", dest="evidence_type",
                             default=None, help="New evidence type descriptor.")
    _gate_edit_shape_group = gate_edit_p.add_mutually_exclusive_group()
    _gate_edit_shape_group.add_argument(
        "--evidence-shape", dest="evidence_shape", default=None,
        help="New evidence shape / check spec.",
    )
    _gate_edit_shape_group.add_argument(
        "--evidence-shape-file", dest="evidence_shape_file", default=None,
        metavar="PATH",
        help=(
            "Read --evidence-shape from PATH. Use '-' to read from stdin. "
            "Recommended when the shape prose contains '<placeholder>' "
            "tokens or '; expect ...' clauses -- inlining that text as a "
            "shell argument can trip the command pre-execution security scan."
        ),
    )
    gate_edit_p.add_argument("--artifact-path", dest="artifact_path",
                             default=None, help="New artifact path for evidence.")
    gate_edit_p.add_argument("--workspace", default=None, metavar="W")
    gate_edit_p.add_argument("--json", action="store_true", default=False,
                             help="Emit JSON.")


def cmd_task(args) -> int:
    """Dispatch handler for `gaia task`."""
    action = getattr(args, "task_action", None)
    handlers = {
        "set-status": _cmd_set_status,
        "add":        _cmd_add,
        "list":       _cmd_list,
        "show":       _cmd_show,
        "remove":     _cmd_remove,
        "edit":       _cmd_edit,
        "reorder":    _cmd_reorder,
        "gate":       _cmd_gate,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia task <set-status|add|list|show|remove|edit|reorder|gate>",
          file=sys.stderr)
    return 0
