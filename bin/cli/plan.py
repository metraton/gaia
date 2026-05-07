"""
gaia plan -- Manage plans (one per brief) in the Gaia DB substrate.

Architecture: Opción B (DB canónica). All mutating operations write only to
``~/.gaia/gaia.db``; nothing under ``.claude/project-context/briefs/`` is
touched. The legacy filesystem-based ``gaia plans`` subcommand (plural) is
retained for read-only display of legacy ``plan.md`` artifacts; this
subcommand (singular) is the canonical writer.

Subcommands:
    gaia plan save --brief=<name> --content="..." [--status=...] [--json]
    gaia plan show <brief-name> [--json]
    gaia plan list [--brief=<name>] [--status=...] [--format=table|json|count]
    gaia plan delete <brief-name> [--yes] [--json]
    gaia plan set-status <brief-name> <new-status> [--json]
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


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_save(args) -> int:
    """Upsert a plan attached to ``--brief``."""
    from gaia.store.writer import upsert_plan, get_plan

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    brief_name = getattr(args, "brief", None)
    content = getattr(args, "content", None)
    status = getattr(args, "status", None) or "draft"
    as_json = getattr(args, "json", False)

    if not brief_name:
        return _err("--brief is required", as_json=as_json)
    if content is None or content == "":
        return _err("--content is required", as_json=as_json)

    try:
        res = upsert_plan(workspace, brief_name, content=content, status=status)
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
          f"(plan_id={plan['id']}, status={plan['status']})")
    print(f"  created_at: {plan['created_at']}")
    print(f"  updated_at: {plan['updated_at']}")
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
    return 0


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
               "  gaia plan save --brief=my-feature --content='...'\n",
    )
    save_p.add_argument("--brief", required=True, metavar="NAME",
                        help="Parent brief slug. Required.")
    save_p.add_argument("--content", required=True,
                        help="Plan markdown body. Required.")
    save_p.add_argument(
        "--status", default=None,
        choices=("draft", "active", "closed"),
        help="Plan status. Default: draft (insert) / preserved (update).",
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


def cmd_plan(args) -> int:
    """Dispatch handler for `gaia plan`."""
    action = getattr(args, "plan_action", None)
    handlers = {
        "save": _cmd_save,
        "show": _cmd_show,
        "list": _cmd_list,
        "delete": _cmd_delete,
        "set-status": _cmd_set_status,
    }
    if action in handlers:
        return handlers[action](args)

    print(
        "Usage: gaia plan <save|show|list|delete|set-status>",
        file=sys.stderr,
    )
    return 0
