"""
gaia plan -- Manage plans (one per brief) in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``; nothing under ``.claude/project-context/briefs/`` is
touched.

Subcommands:
    gaia plan save --brief=<name> (--content="..." | --content-file=<path>)
                   [--status=...] [--reason=...] [--json]
    gaia plan show <brief-name> [--json]
    gaia plan list [--brief=<name>] [--status=...] [--format=table|json|count]
    gaia plan delete <brief-name> [--yes] [--json]
    gaia plan set-status <brief-name> <new-status> [--json]
    gaia plan pause <brief-name> --reason=... | resume <brief-name>
    gaia plan history <brief-name> [--content]
    gaia plan change request <brief-name> --reason=...
    gaia plan change propose <brief-name> <id> --summary=... [--affects=N:WHY ...]
    gaia plan change approve|apply <brief-name> <id> [--content-file=PATH]
    gaia plan change list <brief-name>
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
# Workspace resolution (mirrors brief.py / memory.py)
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


def _read_content_file(path_str: str) -> str:
    """Read plan markdown from a file path or stdin.

    Pass ``"-"`` to read from ``sys.stdin`` until EOF (utf-8). Pass any other
    path string to read that file (utf-8). Raises ``FileNotFoundError`` for
    missing paths (caller converts to _err). Mirrors the helper of the same
    name in bin/cli/brief.py and bin/cli/ac.py -- the established convention
    for long text that must not be inlined as a shell argument.
    """
    if path_str == "-":
        return sys.stdin.read()
    return Path(path_str).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_save(args) -> int:
    """Canonical path to UPSERT a plan into gaia.db.

    Usage
    -----
    ``gaia plan save --brief=<slug> (--content=<md> | --content-file=<path>)
    [--status=draft]``

    This is the ONLY supported way to create or update a plan in gaia.db.
    Every call is idempotent: if no plan exists for the brief it is INSERTed;
    if one already exists its ``status`` and ``content`` fields are UPDATEd.
    The ``plan_id`` assigned on the first insert is permanent -- subsequent
    saves do not change it.

    ``--status`` is optional. Omitted on an INSERT it means ``draft``;
    omitted on an UPDATE it means "keep the status this plan already has",
    so saving new content never changes where the plan is in its lifecycle.

    Scope of the upsert
    -------------------
    ``gaia plan save`` only touches ``plans.status`` and ``plans.content``.
    It is NOT a full-sync over child rows. The ``tasks`` table, acceptance
    criteria rows, and milestones are managed by their own granular writers
    (``add_task_to_plan``, ``remove_task_from_plan``, ``reorder_tasks``).
    Calling ``gaia plan save`` does NOT delete or reorder tasks -- it is safe
    to call repeatedly without losing structured sub-rows.

    Anti-pattern: DO NOT use ``gaia brief edit`` to persist plan content
    --------------------------------------------------------------------
    ``gaia brief edit <slug>`` writes to the ``briefs`` table (brief body
    field), not the ``plans`` table. Plans and briefs are separate rows in
    separate tables. Any content written via ``gaia brief edit`` will NOT
    appear in ``gaia plan show``.

    Specifically, the pattern ``EDITOR=cp /tmp/plan.md gaia brief edit <slug>``
    is a confirmed anti-pattern (observed 2026-05-22). It side-loads content
    into the wrong table, overwrites the brief body, and produces a stale
    brief that does not reflect the plan. Never repeat this. For a body too
    large to pass inline, use ``--content-file``:
    ``gaia plan save --brief=<slug> --content-file=~/.gaia/scratch/plan.md``

    That flag replaced a recommendation to write
    ``--content="$(cat <path>)"``, which no agent caller could follow: a
    command substitution is a shell composition that command-execution
    forbids, so the only documented route for a large body was one its
    intended callers were barred from taking. Five separate turns declined to
    re-emit a ~30 KB plan body through a shell-quoted --content rather than
    risk corrupting the plan of record, and the plan narrative fell behind its
    own rows as a result.

    Verify after saving with ``gaia plan show <slug>``.
    """
    from gaia.store.writer import upsert_plan, get_plan

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = getattr(args, "brief", None)
    content = getattr(args, "content", None)
    content_file = getattr(args, "content_file", None)
    status = getattr(args, "status", None)
    as_json = getattr(args, "json", False)

    if not brief_name:
        return _err("--brief is required", as_json=as_json)

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
        return _err("--content or --content-file is required", as_json=as_json)

    existing = get_plan(workspace, brief_name)
    reason = (getattr(args, "reason", None) or "").strip() or None
    if existing is not None and existing.get("content") != content and reason is None:
        return _err(
            "rewriting an existing plan needs --reason: the version it replaces "
            "is kept with that reason (see `gaia plan history`)",
            as_json=as_json,
        )

    if not status:
        # 'draft' is the default for a NEW plan only. On an update, the live
        # status is preserved: applying the insert default to both paths made
        # every content save silently demote an 'active' plan back to 'draft',
        # and the repair verb (`gaia plan set-status`) is curator-only -- the
        # planner broke a state it had no permission to restore.
        status = (existing or {}).get("status") or "draft"

    try:
        res = upsert_plan(workspace, brief_name, content=content, status=status,
                          reason=reason)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        out = dict(res)
        out["workspace"] = workspace
        print(json.dumps(out, indent=2, default=str))
    else:
        verb = "Created" if res["action"] == "inserted" else "Updated"
        print(f"{verb} plan for brief '{brief_name}' "
              f"(plan_id={res['plan_id']}, status={res['plan_status']})")
    return 0


def _cmd_show(args) -> int:
    from gaia.store.writer import get_plan

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief_name
    as_json = getattr(args, "json", False)

    plan = get_plan(workspace, brief_name)
    if plan is None:
        return _err(
            f"no plan attached to brief '{brief_name}' in workspace "
            f"'{workspace}'",
            as_json=as_json,
        )

    if as_json:
        print(json.dumps(plan, indent=2, default=str))
        return 0

    print(f"Plan for brief '{brief_name}' "
          f"(plan_id={plan['id']}, status={plan['status']}, "
          f"version={plan['version']})")
    if plan.get("pause_reason"):
        print(f"  PAUSED since {plan['paused_at']}: {plan['pause_reason']}")
    print(f"  created_at: {plan['created_at']}")
    print(f"  updated_at: {plan['updated_at']}")
    if plan["version"] > 1:
        print(f"  earlier versions: {plan['version'] - 1} "
              f"(`gaia plan history {brief_name}`)")
    if plan.get("content"):
        print()
        print(plan["content"])
    else:
        print("(no content)")
    return 0


def _cmd_list(args) -> int:
    from gaia.store.writer import list_plans

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = getattr(args, "brief", None)
    status = getattr(args, "status", None)
    fmt = getattr(args, "format", None) or "table"
    if getattr(args, "json", False):
        fmt = "json"

    plans = list_plans(workspace, brief_name=brief_name, status=status)

    if fmt == "count":
        print(len(plans))
        return 0
    if fmt == "json":
        print(json.dumps(plans, indent=2, default=str))
        return 0

    if not plans:
        print("(no plans)")
        return 0
    name_w = max(5, max(len(p["brief_name"]) for p in plans))
    status_w = max(6, max(len(p["status"] or "") for p in plans))
    print(f"{'BRIEF':<{name_w}}  {'STATUS':<{status_w}}  {'UPDATED':<20}")
    print("-" * (name_w + status_w + 24))
    for p in plans:
        print(f"{p['brief_name']:<{name_w}}  "
              f"{(p['status'] or ''):<{status_w}}  "
              f"{(p['updated_at'] or '')[:19]:<20}")
    return 0


def _cmd_delete(args) -> int:
    from gaia.store.writer import get_plan, delete_plan

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief_name
    as_json = getattr(args, "json", False)
    skip_confirm = getattr(args, "yes", False)

    plan = get_plan(workspace, brief_name)
    if plan is None:
        return _err(
            f"no plan attached to brief '{brief_name}' in workspace "
            f"'{workspace}'",
            as_json=as_json,
        )

    if not skip_confirm:
        prompt = (f"Delete plan for brief '{brief_name}' "
                  f"(status={plan['status']})? [y/N] ")
        try:
            answer = input(prompt)
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            if as_json:
                print(json.dumps({"deleted": False, "brief_name": brief_name,
                                  "reason": "aborted by user"}))
            else:
                print(f"Aborted; plan for '{brief_name}' was not deleted.")
            return 0

    deleted = delete_plan(workspace, brief_name)
    if not deleted:
        return _err(
            f"plan for '{brief_name}' could not be deleted (already gone?)",
            as_json=as_json,
        )

    if as_json:
        print(json.dumps({
            "deleted": True,
            "brief_name": brief_name,
            "workspace": workspace,
            "previous_status": plan["status"],
        }, indent=2, default=str))
    else:
        print(f"Deleted plan for brief '{brief_name}' "
              f"(workspace={workspace!r}, previous_status={plan['status']!r})")
    return 0


def _cmd_set_status(args) -> int:
    from gaia.store.writer import set_plan_status

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = args.brief_name
    new_status = args.new_status
    as_json = getattr(args, "json", False)

    try:
        res = set_plan_status(workspace, brief_name, new_status)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps(res, indent=2, default=str))
    else:
        if res.get("action") == "noop":
            print(f"Plan for '{brief_name}' already at status "
                  f"'{new_status}' (noop)")
        else:
            print(f"Plan for '{brief_name}': "
                  f"{res['old_status']} -> {res['new_status']}")
        # D11 advisory: surface unsatisfied ACs on plan close.
        for w in res.get("warnings", []):
            print(f"Warning: {w}", file=sys.stderr)
    return 0


def _run(args, call) -> "tuple[int, dict | None]":
    """Run a writer call, turning a refusal into the CLI's error exit."""
    from gaia.state.permissions import StateTransitionForbidden

    try:
        return 0, call(_resolve_workspace(getattr(args, "workspace", None)))
    except (ValueError, StateTransitionForbidden) as exc:
        return _err(str(exc), as_json=getattr(args, "json", False)), None


def _emit(args, res: dict, line: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2, default=str))
    else:
        print(line)
    return 0


def _cmd_pause(args) -> int:
    from gaia.store.writer import pause_plan

    rc, res = _run(args, lambda ws: pause_plan(ws, args.brief_name, args.reason))
    if rc:
        return rc
    return _emit(args, res, f"Plan for '{args.brief_name}' paused: {res['reason']}")


def _cmd_resume(args) -> int:
    from gaia.store.writer import resume_plan

    rc, res = _run(args, lambda ws: resume_plan(ws, args.brief_name))
    if rc:
        return rc
    return _emit(args, res, f"Plan for '{args.brief_name}' resumed "
                            f"(was paused: {res['was_paused_for']})")


def _cmd_history(args) -> int:
    from gaia.store.writer import get_plan, get_plan_history

    rc, history = _run(args, lambda ws: get_plan_history(ws, args.brief_name))
    if rc:
        return rc
    plan = get_plan(_resolve_workspace(getattr(args, "workspace", None)),
                    args.brief_name)
    if getattr(args, "json", False):
        print(json.dumps({"current_version": plan["version"],
                          "versions": history}, indent=2, default=str))
        return 0
    for v in history:
        change = f" [change #{v['change_id']}]" if v["change_id"] else ""
        print(f"v{v['version']} ({v['status']}) replaced {v['saved_at']}{change}: "
              f"{v['reason'] or '(no reason recorded)'}")
        if getattr(args, "content", False) and v["content"]:
            print(v["content"])
            print()
    print(f"v{plan['version']} (current)")
    return 0


def _parse_affects(values: list[str]) -> list[tuple[int, str]]:
    affected = []
    for value in values or []:
        order, sep, why = value.partition(":")
        if not sep or not order.strip().isdigit():
            raise ValueError(f"--affects expects ORDER:REASON, got {value!r}")
        affected.append((int(order), why))
    return affected


def _cmd_change(args) -> int:
    from gaia.store import writer

    action = getattr(args, "change_action", None)
    brief = args.brief_name
    if action == "list":
        rc, changes = _run(args, lambda ws: writer.list_plan_changes(ws, brief))
        if rc:
            return rc
        if getattr(args, "json", False):
            print(json.dumps(changes, indent=2, default=str))
            return 0
        if not changes:
            print("No plan changes recorded.")
        for c in changes:
            print(f"#{c['id']} {c['status']}: {c['justification']}")
            if c["proposal"]:
                print(f"    proposal: {c['proposal']}")
            for t in c["tasks"]:
                print(f"    task {t['order_num']}: {t['reason']}")
        return 0

    if action == "request":
        call = lambda ws: writer.request_plan_change(ws, brief, args.reason)
    elif action == "propose":
        try:
            affected = _parse_affects(args.affects)
        except ValueError as exc:
            return _err(str(exc), as_json=getattr(args, "json", False))
        call = lambda ws: writer.propose_plan_change(
            ws, brief, args.change_id, args.summary, affected)
    elif action == "approve":
        call = lambda ws: writer.approve_plan_change(ws, brief, args.change_id)
    elif action == "apply":
        content = None
        if args.content_file is not None:
            try:
                content = _read_content_file(args.content_file)
            except OSError as exc:
                return _err(f"--content-file: {exc}",
                            as_json=getattr(args, "json", False))
        call = lambda ws: writer.apply_plan_change(
            ws, brief, args.change_id, content=content)
    else:
        print("Usage: gaia plan change <request|propose|approve|apply|list>",
              file=sys.stderr)
        return 0

    rc, res = _run(args, call)
    if rc:
        return rc
    line = f"Plan change #{res['change_id']} for '{brief}': {res['status']}"
    if action == "apply":
        line += f" -> plan version {res['version']}"
        for t in res["stale_tasks"]:
            derived = t.get("derived_closure") or {}
            line += (f"\n  task {t['order_num']}: verdicts stale"
                     f" ({derived.get('action') or 'no transition'})")
    return _emit(args, res, line)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `plan` (singular) subcommand with the root parser."""
    plan_parser = subparsers.add_parser(
        "plan",
        help="Manage plans (one per brief, DB-canonical)",
        description=(
            "Save, show, list, delete, and transition plans attached to "
            "briefs. One plan per brief."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plan_parser.add_argument(
        "--workspace", metavar="W", default=None,
        help="Workspace identity. Default: gaia.project.current() or 'me'.",
    )

    actions = plan_parser.add_subparsers(dest="plan_action", metavar="<action>")

    # -- save ---------------------------------------------------------------
    save_p = actions.add_parser(
        "save",
        help="Upsert the plan attached to a brief",
        description="Insert or update the plan row for (workspace, brief).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  gaia plan save --brief=my-feature --content='...'\n"
               "  gaia plan save --brief=my-feature "
               "--content-file=~/.gaia/scratch/plan.md\n"
               "  cat plan.md | gaia plan save --brief=my-feature "
               "--content-file=-\n",
    )
    save_p.add_argument("--brief", required=True, metavar="NAME",
                        help="Parent brief slug. Required.")
    save_content_source = save_p.add_mutually_exclusive_group(required=True)
    save_content_source.add_argument(
        "--content", metavar="MARKDOWN",
        help="Plan markdown body, inline. Use --content-file for a large body.",
    )
    save_content_source.add_argument(
        "--content-file", dest="content_file", metavar="PATH",
        help=(
            "Read the plan markdown from PATH instead of a shell argument "
            "('-' reads stdin). This is the channel for a multi-kilobyte body, "
            "and for any body carrying quotes, backticks or '$' -- pipe it on "
            "stdin with a quoted heredoc (--content-file=- <<'PLAN'), which "
            "needs no file and no Write tool, rather than re-emitting the "
            "whole plan through one shell-quoted --content value, where a "
            "single unescaped character silently corrupts the plan of record."
        ),
    )
    save_p.add_argument(
        "--status", default=None,
        choices=("draft", "active", "closed"),
        help="Plan status. Default: draft (insert) / preserved (update).",
    )
    save_p.add_argument(
        "--reason", default=None,
        help=("Why the plan changes. Required when it rewrites an existing "
              "plan's content: the replaced version is kept with this reason."),
    )
    save_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    save_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON. bool.")

    # -- show ---------------------------------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Print the plan attached to a brief",
        description="Print the plan markdown with a one-line header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia plan show my-feature\n",
    )
    show_p.add_argument("brief_name", help="Parent brief slug.")
    show_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")
    show_p.add_argument("--json", action="store_true", default=False,
                        help="Emit JSON. bool.")

    # -- list ---------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List plans in the workspace",
        description="List plan rows, optionally filtered by brief or status.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia plan list --status=active\n",
    )
    list_p.add_argument("--brief", default=None, metavar="NAME",
                        help="Filter by parent brief slug.")
    list_p.add_argument("--status", default=None,
                        choices=("draft", "active", "closed"),
                        help="Filter by plan status.")
    list_p.add_argument("--format", default="table",
                        choices=("table", "json", "count"),
                        help="Output shape. Default: table.")
    list_p.add_argument("--json", action="store_true", default=False,
                        help="Alias for --format=json.")
    list_p.add_argument("--workspace", default=None, metavar="W",
                        help="Workspace identity.")

    # -- delete -------------------------------------------------------------
    delete_p = actions.add_parser(
        "delete",
        help="Hard-delete the plan attached to a brief",
        description="Drop the plan row; parent brief is preserved.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia plan delete my-feature --yes\n",
    )
    delete_p.add_argument("brief_name", help="Parent brief slug.")
    delete_p.add_argument("--yes", action="store_true", default=False,
                          help="Skip confirm prompt. bool. Default: false.")
    delete_p.add_argument("--workspace", default=None, metavar="W",
                          help="Workspace identity.")
    delete_p.add_argument("--json", action="store_true", default=False,
                          help="Emit JSON. bool.")

    # -- set-status ---------------------------------------------------------
    setstatus_p = actions.add_parser(
        "set-status",
        help="Transition plan status",
        description="State machine: draft -> active -> closed.",
    )
    setstatus_p.add_argument("brief_name", help="Parent brief slug.")
    setstatus_p.add_argument(
        "new_status",
        choices=("draft", "active", "closed"),
        help="Target status.",
    )
    setstatus_p.add_argument("--workspace", default=None, metavar="W",
                             help="Workspace identity.")
    setstatus_p.add_argument("--json", action="store_true", default=False,
                             help="Emit JSON. bool.")

    def _common(p):
        p.add_argument("--workspace", default=None, metavar="W",
                       help="Workspace identity.")
        p.add_argument("--json", action="store_true", default=False,
                       help="Emit JSON.")

    # -- pause / resume -------------------------------------------------------
    pause_p = actions.add_parser(
        "pause", help="Stop an active plan; its tasks are not dispatched",
        description=("Pause an active plan with a required reason. The plan "
                     "stays approved (status active); task_execution dispatches "
                     "on it are refused at contract birth until it is resumed."),
    )
    pause_p.add_argument("brief_name", help="Parent brief slug.")
    pause_p.add_argument("--reason", required=True, help="Why the plan stops.")
    _common(pause_p)

    resume_p = actions.add_parser(
        "resume", help="Resume a paused plan where it stopped")
    resume_p.add_argument("brief_name", help="Parent brief slug.")
    _common(resume_p)

    # -- history ----------------------------------------------------------------
    history_p = actions.add_parser(
        "history", help="List the plan's replaced versions and why",
        description="Every earlier version of the plan with its replacement reason.",
    )
    history_p.add_argument("brief_name", help="Parent brief slug.")
    history_p.add_argument("--content", action="store_true", default=False,
                           help="Also print each earlier version's content.")
    _common(history_p)

    # -- change <request|propose|approve|apply|list> ---------------------------
    change_p = actions.add_parser(
        "change", help="Managed plan change: request, propose, approve, apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "The orchestrator requests a change with its justification; the "
            "planner proposes which tasks it affects and why; the orchestrator "
            "approves; the planner applies it as a new plan version, which "
            "marks stale only the affected tasks' verdicts."
        ),
        epilog=(
            "Examples:\n"
            "  gaia plan change request my-brief --reason='the API moved to v2'\n"
            "  gaia plan change propose my-brief 4 --summary='re-point task 2' "
            "--affects='2:calls the old endpoint'\n"
            "  gaia plan change approve my-brief 4\n"
            "  gaia plan change apply my-brief 4 --content-file=- <<'PLAN'\n"
        ),
    )
    c_actions = change_p.add_subparsers(dest="change_action", metavar="<action>")
    c_request = c_actions.add_parser("request", help="Request a change (orchestrator)")
    c_request.add_argument("brief_name", help="Parent brief slug.")
    c_request.add_argument("--reason", required=True,
                           help="Justification for the change.")
    _common(c_request)
    c_propose = c_actions.add_parser("propose", help="Propose the delta (planner)")
    c_propose.add_argument("brief_name", help="Parent brief slug.")
    c_propose.add_argument("change_id", type=int, help="Plan change id.")
    c_propose.add_argument("--summary", required=True, help="What changes.")
    c_propose.add_argument("--affects", action="append", default=[],
                           metavar="ORDER:REASON",
                           help="An affected task and why. Repeatable.")
    _common(c_propose)
    c_approve = c_actions.add_parser("approve", help="Approve the proposal (orchestrator)")
    c_approve.add_argument("brief_name", help="Parent brief slug.")
    c_approve.add_argument("change_id", type=int, help="Plan change id.")
    _common(c_approve)
    c_apply = c_actions.add_parser("apply", help="Apply an approved change (planner)")
    c_apply.add_argument("brief_name", help="Parent brief slug.")
    c_apply.add_argument("change_id", type=int, help="Plan change id.")
    c_apply.add_argument("--content-file", dest="content_file", default=None,
                         metavar="PATH",
                         help="New plan content ('-' reads stdin). Omit to keep it.")
    _common(c_apply)
    c_list = c_actions.add_parser("list", help="List the plan's changes")
    c_list.add_argument("brief_name", help="Parent brief slug.")
    _common(c_list)


def cmd_plan(args) -> int:
    """Dispatch handler for `gaia plan`."""
    action = getattr(args, "plan_action", None)
    handlers = {
        "save": _cmd_save,
        "show": _cmd_show,
        "list": _cmd_list,
        "delete": _cmd_delete,
        "set-status": _cmd_set_status,
        "pause": _cmd_pause,
        "resume": _cmd_resume,
        "history": _cmd_history,
        "change": _cmd_change,
    }
    if action in handlers:
        return handlers[action](args)

    print(
        "Usage: gaia plan <save|show|list|delete|set-status|pause|resume|"
        "history|change>",
        file=sys.stderr,
    )
    return 0
