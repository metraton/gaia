"""
gaia notifications -- the headless scheduled-task inbox in the Gaia DB substrate.

A headless scheduled task (see the `scheduled-task` skill) runs unattended and
cannot ask anything mid-run, so when it finishes it leaves ONE report row here
via `gaia notifications add`: a generic, PII-free summary plus any accumulated
approval_ids, keyed by the resumable Claude session_id. The user sees an unread
counter each prompt and a compact list at SessionStart, then reads the detail
and resumes on demand.

Architecture: Opción B (DB canónica). All operations go through the typed API
in gaia.store.{writer,reader} -- never raw SQL. Writes are episodic (not curated
memory), so no agent_permissions gate. The whole group classifies T0 (local,
reversible bookkeeping) via COMMAND_SUBCOMMAND_TIER_EXCEPTIONS in
hooks/modules/security/mutative_verbs.py.

Subcommands:
    gaia notifications add --task NAME --headline "..." [--body "..."]
                           [--session-id SID] [--workspace W] [--json]
    gaia notifications list [--unread] [--all-workspaces] [--limit N]
                            [--workspace W] [--json]
    gaia notifications show <id> [--json]
    gaia notifications ack <id> [--json]
    gaia notifications ack --all [--all-workspaces] [--workspace W] [--json]
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


def _fmt_row_line(row: dict) -> str:
    """One compact line for `list`: [id] task — headline (created_at) sid."""
    sid = row.get("session_id") or "-"
    return (
        f"[{row['id']}] {row['task_name']} — {row['headline']} "
        f"({row.get('created_at', '?')}) resume: {sid}"
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_add(args) -> int:
    from gaia.store.writer import add_task_notification

    as_json = getattr(args, "json", False)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    try:
        new_id = add_task_notification(
            task_name=args.task,
            headline=args.headline,
            body=getattr(args, "body", None),
            session_id=getattr(args, "session_id", None),
            workspace=workspace,
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if as_json:
        print(json.dumps({"status": "ok", "id": new_id, "workspace": workspace}))
    else:
        print(f"Added task notification #{new_id} for task '{args.task}'")
    return 0


def _cmd_list(args) -> int:
    from gaia.store.reader import list_unread_notifications
    from gaia.store.writer import _connect
    from gaia.store.writer import _now_iso  # noqa: F401 (import surface parity)

    as_json = getattr(args, "json", False)
    all_ws = getattr(args, "all_workspaces", False)
    workspace = None if all_ws else _resolve_workspace(getattr(args, "workspace", None))
    limit = getattr(args, "limit", 50) or 50
    unread_only = getattr(args, "unread", False)

    if unread_only:
        rows = list_unread_notifications(workspace=workspace, limit=limit)
    else:
        # Full list (read + unread), newest first. Kept inline: the reader's
        # public surface is the hot unread path; the "show everything" variant
        # is a rare CLI convenience, so it does its own read-only SELECT.
        try:
            con = _connect(None)
        except Exception as exc:
            return _err(f"db unavailable: {exc}", as_json=as_json)
        try:
            if workspace is None:
                cur = con.execute(
                    "SELECT * FROM task_notifications "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                )
            else:
                cur = con.execute(
                    "SELECT * FROM task_notifications WHERE workspace = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (workspace, limit),
                )
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            con.close()

    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    if not rows:
        scope = "unread " if unread_only else ""
        print(f"No {scope}task notifications.")
        return 0

    for row in rows:
        mark = "" if row.get("unread") else " (seen)"
        print(_fmt_row_line(row) + mark)
    return 0


def _cmd_show(args) -> int:
    from gaia.store.reader import get_notification

    as_json = getattr(args, "json", False)
    row = get_notification(args.id)
    if row is None:
        return _err(f"no notification with id {args.id}", as_json=as_json)

    if as_json:
        print(json.dumps(row, indent=2, default=str))
        return 0

    print(f"# Task notification #{row['id']}")
    print(f"task_name:  {row['task_name']}")
    print(f"headline:   {row['headline']}")
    print(f"created_at: {row.get('created_at', '?')}")
    print(f"workspace:  {row.get('workspace') or '-'}")
    print(f"session_id: {row.get('session_id') or '-'}")
    print(f"unread:     {bool(row.get('unread'))}")
    if row.get("acked_at"):
        print(f"acked_at:   {row['acked_at']}")
    if row.get("session_id"):
        print(f"\nResume with: claude --resume {row['session_id']}")
    print("\n--- body ---")
    print(row.get("body") or "(no body)")
    return 0


def _cmd_ack(args) -> int:
    from gaia.store.writer import ack_task_notification, ack_all_task_notifications

    as_json = getattr(args, "json", False)

    if getattr(args, "all", False):
        all_ws = getattr(args, "all_workspaces", False)
        workspace = None if all_ws else _resolve_workspace(getattr(args, "workspace", None))
        cleared = ack_all_task_notifications(workspace=workspace)
        if as_json:
            print(json.dumps({"status": "ok", "acked": cleared}))
        else:
            print(f"Acknowledged {cleared} notification(s).")
        return 0

    if args.id is None:
        return _err("ack requires an <id> or --all", as_json=as_json)

    res = ack_task_notification(args.id)
    if res.get("status") == "not_found":
        return _err(f"no notification with id {args.id}", as_json=as_json)
    if as_json:
        print(json.dumps(res))
    else:
        action = res.get("action")
        if action == "noop":
            print(f"Notification #{args.id} was already seen (noop).")
        else:
            print(f"Acknowledged notification #{args.id}.")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `notifications` subcommand with the root parser."""
    p = subparsers.add_parser(
        "notifications",
        help="Headless scheduled-task inbox (add/list/show/ack)",
        description="Manage task notifications left by headless scheduled tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="notifications_action", metavar="<action>")

    # -- add -------------------------------------------------------------------
    add_p = actions.add_parser(
        "add",
        help="Add a task notification (called by a finished headless task)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia notifications add --task nightly-tests "
            "--headline 'He terminado la tarea: 2 fallos' \\\n"
            "    --body '...' --session-id abc123\n"
        ),
    )
    add_p.add_argument("--task", required=True, metavar="NAME",
                       help="Name of the scheduled task producing the report.")
    add_p.add_argument("--headline", required=True,
                       help="Short one-line summary (the title).")
    add_p.add_argument("--body", default=None,
                       help="Full detail message (generic; no PII).")
    add_p.add_argument("--session-id", dest="session_id", default=None,
                       metavar="SID", help="Resumable Claude session id.")
    add_p.add_argument("--workspace", default=None, metavar="W")
    add_p.add_argument("--json", action="store_true", default=False)

    # -- list ------------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List task notifications (default: current workspace)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia notifications list --unread\n"
            "  gaia notifications list --all-workspaces --json\n"
        ),
    )
    list_p.add_argument("--unread", action="store_true", default=False,
                        help="Only notifications not yet acknowledged.")
    list_p.add_argument("--all-workspaces", dest="all_workspaces",
                        action="store_true", default=False,
                        help="Across all workspaces (default: current only).")
    list_p.add_argument("--limit", type=int, default=50, metavar="N")
    list_p.add_argument("--workspace", default=None, metavar="W")
    list_p.add_argument("--json", action="store_true", default=False)

    # -- show ------------------------------------------------------------------
    show_p = actions.add_parser(
        "show",
        help="Show the full detail of one notification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument("id", type=int, metavar="ID", help="Notification id.")
    show_p.add_argument("--json", action="store_true", default=False)

    # -- ack -------------------------------------------------------------------
    ack_p = actions.add_parser(
        "ack",
        help="Mark a notification (or --all) as seen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia notifications ack 3\n"
            "  gaia notifications ack --all\n"
        ),
    )
    ack_p.add_argument("id", type=int, nargs="?", default=None, metavar="ID",
                       help="Notification id to acknowledge.")
    ack_p.add_argument("--all", action="store_true", default=False,
                       help="Acknowledge every unread notification.")
    ack_p.add_argument("--all-workspaces", dest="all_workspaces",
                       action="store_true", default=False,
                       help="With --all: across all workspaces.")
    ack_p.add_argument("--workspace", default=None, metavar="W")
    ack_p.add_argument("--json", action="store_true", default=False)


def cmd_notifications(args) -> int:
    """Dispatch handler for `gaia notifications`."""
    action = getattr(args, "notifications_action", None)
    handlers = {
        "add":  _cmd_add,
        "list": _cmd_list,
        "show": _cmd_show,
        "ack":  _cmd_ack,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia notifications <add|list|show|ack>", file=sys.stderr)
    return 0
