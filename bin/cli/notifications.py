"""
gaia notifications -- reports, reminders and routines in the Gaia DB substrate.

One concept, the notification, in three kinds: a report a task or agent left
behind, a one-shot reminder, and a recurring routine. A reminder or routine may
point at a skill, a memory or a project. Nothing runs unattended: a row is due
when a read finds it open with its due time passed, and the user meets it at
session start or on the per-prompt counter. Reads never write.

Architecture: all operations go through the typed API in
gaia.store.{writer,reader}. Writes are episodic (not curated memory), so no
agent_permissions gate. The whole group classifies T0 (local, reversible
bookkeeping) via COMMAND_SUBCOMMAND_TIER_EXCEPTIONS in
hooks/modules/security/mutative_verbs.py.

Subcommands:
    gaia notifications add --kind reminder --at WHEN --headline "..." [POINTER]
    gaia notifications add --kind routine (--cron EXPR | --every DUR) --headline "..." [POINTER]
    gaia notifications add --task NAME --headline "..." [--body "..."] [--session-id SID]
    gaia notifications list [--unread | --upcoming] [--all-workspaces] [--limit N]
                            [--workspace W] [--json]
    gaia notifications show <id> [--json]
    gaia notifications ack <id> [--json]
    gaia notifications ack --all [--all-workspaces] [--workspace W] [--json]
    gaia notifications snooze <id> (--for DUR | --until WHEN) [--json]
    gaia notifications cancel <id> [--json]

POINTER is one of --skill NAME, --memory WORKSPACE:SLUG, --project WORKSPACE/NAME.
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
    from gaia.project import cli_workspace
    return cli_workspace(explicit)


def _err(msg: str, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(f"Error: {msg}", file=sys.stderr)
    return 1


def _fmt_row_line(row: dict) -> str:
    """One compact line for `list`."""
    from gaia import notifications_time as clock

    if row.get("kind", "report") != "report":
        return clock.summary(row)
    sid = row.get("session_id") or "-"
    return (
        f"[{row['id']}] {row['task_name']} — {row['headline']} "
        f"({row.get('created_at', '?')}) session: {sid}"
    )


def _schedule(args, now) -> tuple[str | None, dict | None]:
    """Return ``(due_at, recurrence)`` for the kind asked, or raise ValueError."""
    from gaia import notifications_time as clock

    given = [flag for flag, value in (("--at", args.at), ("--cron", args.cron),
                                      ("--every", args.every)) if value]
    if args.kind == "report":
        if given:
            raise ValueError(f"a report takes no {given[0]}; use --kind reminder or routine")
        return None, None
    if args.kind == "reminder":
        if given != ["--at"]:
            raise ValueError("a reminder takes exactly --at WHEN")
        return clock.to_iso(clock.parse_at(args.at, now)), None
    if len(given) != 1 or given[0] == "--at":
        raise ValueError("a routine takes exactly one of --cron EXPR or --every DUR")
    spec = clock.cron_spec(args.cron) if args.cron else clock.interval_spec(args.every)
    return clock.to_iso(clock.next_occurrence(spec, now)), spec


def _pointer(args) -> tuple[str, str, str | None] | None:
    """Return the validated ``(kind, ref, workspace)`` pointer, or raise ValueError."""
    given = [value for value in (args.skill, args.memory, args.project) if value]
    if len(given) > 1:
        raise ValueError("give at most one of --skill, --memory, --project")
    if args.skill:
        if not (_REPO_ROOT / "skills" / args.skill / "SKILL.md").is_file():
            raise ValueError(f"no skill named {args.skill!r}")
        return "skill", args.skill, None
    if args.memory:
        workspace, _, slug = args.memory.partition(":")
        if not workspace or not slug:
            raise ValueError("--memory takes WORKSPACE:SLUG")
        from gaia.store.writer import HOST_WORKSPACE, USER_WORKSPACE, get_memory
        holders = dict.fromkeys((workspace, USER_WORKSPACE, HOST_WORKSPACE))
        if not any(get_memory(holder, slug) is not None for holder in holders):
            raise ValueError(f"no memory {slug!r} in workspace {workspace!r}")
        return "memory", slug, workspace
    if args.project:
        workspace, _, name = args.project.partition("/")
        if not workspace or not name:
            raise ValueError("--project takes WORKSPACE/NAME")
        from gaia.store.writer import resolve_project_ref
        resolve_project_ref(workspace, name)
        return "project", name, workspace
    return None


def _due_payload(due_at: str | None) -> dict:
    from gaia import notifications_time as clock

    if not due_at:
        return {}
    return {"due_at": due_at, "due_local": clock.format_local(clock.from_iso(due_at))}


def _json_row(row: dict, now) -> dict:
    """A notification row as JSON readers get it: due_local and now_local beside due_at."""
    from gaia import notifications_time as clock

    return {**row, **_due_payload(row.get("due_at")), "now_local": clock.format_local(now)}


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_add(args) -> int:
    from gaia import notifications_time as clock
    from gaia.store.writer import add_task_notification

    as_json = getattr(args, "json", False)
    if args.kind == "report" and not args.task:
        return _err("a report needs --task NAME", as_json=as_json)
    now = clock.now_utc()
    try:
        due_at, recurrence = _schedule(args, now)
        pointer = _pointer(args)
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    workspace = (
        _resolve_workspace(args.workspace) if args.kind == "report" else args.workspace
    )
    try:
        new_id = add_task_notification(
            task_name=args.task or args.kind,
            headline=args.headline,
            body=args.body,
            session_id=args.session_id,
            workspace=workspace,
            kind=args.kind,
            due_at=due_at,
            recurrence=recurrence,
            pointer=pointer,
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    due = _due_payload(due_at)
    if as_json:
        print(json.dumps({"status": "ok", "id": new_id, "kind": args.kind,
                          "workspace": workspace, **due,
                          "now_local": clock.format_local(now)}))
    elif due:
        print(f"Added {args.kind} #{new_id}, due {due['due_local']} (local time)")
        print(clock.clock_line(now))
    else:
        print(f"Added task notification #{new_id} for task '{args.task}'")
    return 0


def _cmd_list(args) -> int:
    from gaia import notifications_time as clock
    from gaia.store.reader import (
        list_unread_notifications, list_upcoming_notifications, notification_scope,
    )
    from gaia.store.writer import _connect

    as_json = getattr(args, "json", False)
    all_ws = getattr(args, "all_workspaces", False)
    workspace = None if all_ws else _resolve_workspace(getattr(args, "workspace", None))
    limit = getattr(args, "limit", 50) or 50
    unread_only = getattr(args, "unread", False)

    if unread_only:
        rows = list_unread_notifications(workspace=workspace, limit=limit)
    elif getattr(args, "upcoming", False):
        rows = list_upcoming_notifications(workspace=workspace, limit=limit)
    else:
        # Full list (open + closed), newest first: a rare CLI convenience, so
        # it does its own read-only SELECT instead of widening the reader.
        try:
            con = _connect(None)
        except Exception as exc:
            return _err(f"db unavailable: {exc}", as_json=as_json)
        try:
            scope_sql, scope_params = notification_scope(con, workspace)
            cur = con.execute(
                f"SELECT * FROM task_notifications WHERE 1 = 1{scope_sql} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (*scope_params, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            con.close()

    now = clock.now_utc()
    if as_json:
        print(json.dumps([_json_row(row, now) for row in rows], indent=2, default=str))
        return 0

    if not rows:
        print("No notifications.")
    for row in rows:
        closed = row.get("closed_at") or (row.get("kind", "report") == "report"
                                          and not row.get("unread"))
        print(_fmt_row_line(row) + (" (seen)" if closed else ""))
    print(clock.clock_line(now))
    return 0


def _cmd_show(args) -> int:
    from gaia import notifications_time as clock
    from gaia.store.reader import get_notification

    as_json = getattr(args, "json", False)
    row = get_notification(args.id)
    if row is None:
        return _err(f"no notification with id {args.id}", as_json=as_json)

    now = clock.now_utc()
    if as_json:
        print(json.dumps(_json_row(row, now), indent=2, default=str))
        return 0

    print(f"# Notification #{row['id']} ({row.get('kind', 'report')})")
    print(clock.clock_line(now))
    print(f"task_name:  {row['task_name']}")
    print(f"headline:   {row['headline']}")
    print(f"created_at: {row.get('created_at', '?')}")
    print(f"workspace:  {row.get('workspace') or '-'}")
    print(f"session_id: {row.get('session_id') or '-'}")
    print(f"unread:     {bool(row.get('unread'))}")
    if row.get("due_at"):
        print(f"due:        {clock.format_local(clock.from_iso(row['due_at']))}")
    if row.get("recurrence"):
        print(f"recurrence: {row['recurrence']}")
    if row.get("pointer_kind"):
        print(f"points at:  {clock.pointer_label(row)}")
    if row.get("acked_at"):
        print(f"acked_at:   {row['acked_at']}")
    if row.get("closed_at"):
        print(f"closed_at:  {row['closed_at']}")
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
            print(f"Acknowledged {cleared} report(s).")
        return 0

    if args.id is None:
        return _err("ack requires an <id> or --all", as_json=as_json)

    res = ack_task_notification(args.id)
    if res.get("status") == "not_found":
        return _err(f"no notification with id {args.id}", as_json=as_json)
    if as_json:
        print(json.dumps({**res, **_due_payload(res.get("due_at"))}))
    elif res.get("action") == "noop":
        print(f"Notification #{args.id} was already closed (noop).")
    elif res.get("action") == "advanced":
        print(f"Done; routine #{args.id} next due {_due_payload(res['due_at'])['due_local']}.")
    else:
        print(f"Acknowledged notification #{args.id}.")
    return 0


def _cmd_snooze(args) -> int:
    from gaia import notifications_time as clock
    from gaia.store.writer import snooze_task_notification

    as_json = getattr(args, "json", False)
    now = clock.now_utc()
    try:
        until = (now + clock.parse_duration(args.for_) if args.for_
                 else clock.parse_at(args.until, now))
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)
    res = snooze_task_notification(args.id, until=clock.to_iso(until))
    if res.get("status") == "not_found":
        return _err(f"no open notification with id {args.id}", as_json=as_json)
    due = _due_payload(res["due_at"])
    if as_json:
        print(json.dumps({**res, **due, "now_local": clock.format_local(now)}))
    else:
        print(f"Notification #{args.id} snoozed until {due['due_local']}.")
        print(clock.clock_line(now))
    return 0


def _cmd_cancel(args) -> int:
    from gaia.store.writer import cancel_task_notification

    as_json = getattr(args, "json", False)
    res = cancel_task_notification(args.id)
    if res.get("status") == "not_found":
        return _err(f"no open notification with id {args.id}", as_json=as_json)
    if as_json:
        print(json.dumps(res))
    else:
        print(f"Notification #{args.id} cancelled; it will not come back.")
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(subparsers) -> None:
    """Register the `notifications` subcommand with the root parser."""
    p = subparsers.add_parser(
        "notifications",
        help="Reports, reminders and routines (add/list/show/ack/snooze/cancel)",
        description=(
            "Manage notifications: reports left by tasks, one-shot reminders and "
            "recurring routines. Nothing runs unattended; a reminder is due when "
            "Gaia is next used after its time."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = p.add_subparsers(dest="notifications_action", metavar="<action>")

    # -- add -------------------------------------------------------------------
    add_p = actions.add_parser(
        "add",
        help="Add a report, a reminder or a routine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run `gaia now` first and compute the absolute local --at from its output.\n"
            "A time without a zone is local time. A reminder or routine is global\n"
            "(seen from every workspace) unless --workspace is given.\n\n"
            "Examples:\n"
            "  gaia notifications add --kind reminder --at 2026-10-01T16:00 "
            "--headline 'Review X'\n"
            "  gaia notifications add --kind routine --cron '0 17 * * 5' "
            "--headline 'Load timesheet' \\\n"
            "    --memory aaxis:project_aaxis_timesheet\n"
            "  gaia notifications add --kind routine --every 6h --headline 'Triage mail' "
            "--skill gmail-triage\n"
            "  gaia notifications add --task nightly-tests --headline '2 failures' "
            "--body '...'\n"
        ),
    )
    add_p.add_argument("--kind", choices=("report", "reminder", "routine"), default="report",
                       help="What is added (default: report).")
    add_p.add_argument("--task", default=None, metavar="NAME",
                       help="Report: name of the task producing it.")
    add_p.add_argument("--headline", required=True,
                       help="Short one-line summary (the title).")
    add_p.add_argument("--body", default=None,
                       help="Full detail message (generic; no PII).")
    add_p.add_argument("--session-id", dest="session_id", default=None,
                       metavar="SID", help="Report: host session it came from.")
    add_p.add_argument("--at", default=None, metavar="WHEN",
                       help="Reminder: when it comes due, e.g. 2026-10-01T16:00.")
    add_p.add_argument("--cron", default=None, metavar="EXPR",
                       help="Routine: five-field cron expression in local time.")
    add_p.add_argument("--every", default=None, metavar="DUR",
                       help="Routine: fixed interval such as 90m, 6h, 1d, 1w.")
    add_p.add_argument("--skill", default=None, metavar="NAME",
                       help="Point at a Gaia skill.")
    add_p.add_argument("--memory", default=None, metavar="WORKSPACE:SLUG",
                       help="Point at a curated memory.")
    add_p.add_argument("--project", default=None, metavar="WORKSPACE/NAME",
                       help="Point at a registered project.")
    add_p.add_argument("--workspace", default=None, metavar="W")
    add_p.add_argument("--json", action="store_true", default=False)

    # -- list ------------------------------------------------------------------
    list_p = actions.add_parser(
        "list",
        help="List notifications (default: current workspace and global ones)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia notifications list --unread\n"
            "  gaia notifications list --upcoming\n"
            "  gaia notifications list --all-workspaces --json\n"
        ),
    )
    shown = list_p.add_mutually_exclusive_group()
    shown.add_argument("--unread", action="store_true", default=False,
                       help="Only what is open and due now.")
    shown.add_argument("--upcoming", action="store_true", default=False,
                       help="Open reminders and routines, soonest first.")
    list_p.add_argument("--all-workspaces", dest="all_workspaces",
                        action="store_true", default=False,
                        help="Across all workspaces (default: current and global).")
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
        help="Mark done: a report or reminder closes, a routine moves to its next time",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--all sweeps reports only; reminders and routines are acknowledged by id.\n\n"
            "Examples:\n"
            "  gaia notifications ack 3\n"
            "  gaia notifications ack --all\n"
        ),
    )
    ack_p.add_argument("id", type=int, nargs="?", default=None, metavar="ID",
                       help="Notification id to acknowledge.")
    ack_p.add_argument("--all", action="store_true", default=False,
                       help="Acknowledge every unread report.")
    ack_p.add_argument("--all-workspaces", dest="all_workspaces",
                       action="store_true", default=False,
                       help="With --all: across all workspaces.")
    ack_p.add_argument("--workspace", default=None, metavar="W")
    ack_p.add_argument("--json", action="store_true", default=False)

    # -- snooze ----------------------------------------------------------------
    snooze_p = actions.add_parser(
        "snooze",
        help="Hide an open notification until later",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia notifications snooze 3 --for 2h\n"
            "  gaia notifications snooze 3 --until 2026-10-02T09:00\n"
        ),
    )
    snooze_p.add_argument("id", type=int, metavar="ID", help="Notification id.")
    until = snooze_p.add_mutually_exclusive_group(required=True)
    until.add_argument("--for", dest="for_", default=None, metavar="DUR",
                       help="How long, such as 90m, 2h, 1d.")
    until.add_argument("--until", default=None, metavar="WHEN",
                       help="Local time it comes back.")
    snooze_p.add_argument("--json", action="store_true", default=False)

    # -- cancel ----------------------------------------------------------------
    cancel_p = actions.add_parser(
        "cancel",
        help="Close an open notification for good; a routine does not come back",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cancel_p.add_argument("id", type=int, metavar="ID", help="Notification id.")
    cancel_p.add_argument("--json", action="store_true", default=False)


def cmd_notifications(args) -> int:
    """Dispatch handler for `gaia notifications`."""
    action = getattr(args, "notifications_action", None)
    handlers = {
        "add":    _cmd_add,
        "list":   _cmd_list,
        "show":   _cmd_show,
        "ack":    _cmd_ack,
        "snooze": _cmd_snooze,
        "cancel": _cmd_cancel,
    }
    if action in handlers:
        return handlers[action](args)

    print("Usage: gaia notifications <add|list|show|ack|snooze|cancel>", file=sys.stderr)
    return 0
