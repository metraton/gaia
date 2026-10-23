"""gaia map -- what has live state now with the verb that moves it, the habits to keep unasked, and which capability serves an intent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LISTED_IDS = 5


class Intent(NamedTuple):
    key: str
    want: str
    use: str
    command: str
    example: str


HABITS = (
    ("the user voices a doubt, an idea or a pending",
     "Skill memory before the first verb; its Where it goes router picks the home; say where in one line"),
    ("a time or a date is mentioned beside something to do",
     "offer a reminder in one line; on their yes, Skill reminders"),
    ("ids arrive in a return (contract, handoff, plan, slug)",
     "append them to the project's thread row before answering"),
    ("a project's depth is needed, or a (N) count sits by its name",
     "gaia context project <name>"),
    ("the intent needs a capability the user did not name",
     "offer it in one line after the answer, at most one per turn; act only on their yes"),
)

INTENTS = (
    Intent("remember", "remember / find / curate", "Skill memory",
           "gaia memory add|append|search|show",
           "gaia memory search 'release checklist'"),
    Intent("remind", "be reminded, or a routine", "Skill reminders",
           "gaia notifications add --kind reminder --at WHEN | --kind routine --cron EXPR|--every DUR",
           "gaia notifications add --kind routine --cron '0 17 * * 5' --headline 'Load the timesheet'"),
    Intent("capture", "capture a feature", "Skill brief-spec",
           "gaia brief new --headless --title=... --objective=...",
           "gaia brief new --headless --title='Export to CSV' --objective='...'"),
    Intent("plan", "plan a brief", "gaia-planner",
           "dispatch gaia-planner with the brief name; read the result with gaia plan show <brief>",
           "gaia plan show export-to-csv"),
    Intent("project", "know one project", "gaia context project",
           "gaia context project <name>",
           "gaia context project gaia"),
    Intent("history", "what happened, events", "gaia query",
           "gaia query --since=<window> [--failed] [--group-by=...]",
           "gaia query --since=24h --failed"),
    Intent("explain", "explain / document / ticket", "technical-explanation | technical-writing | ticket-writing",
           "Skill technical-explanation (technical-writing for a README, runbook, PR body or report; "
           "ticket-writing for a ticket)",
           "Skill technical-writing before rewriting a stale README"),
    Intent("deck", "a diagram deck (architecture, timeline, flow)", "Skill deck-builder",
           "Skill deck-builder for the phases; dispatch the approved per-page tables naming deck-builder "
           "and technical-explanation",
           "an architecture map of the release flow: Skill deck-builder"),
    Intent("review", "review a PR or branch", "Skill code-review",
           "Skill code-review, then dispatch the reviewers it names",
           "review branch feat/x: dispatch gaia-system with Skill code-review"),
    Intent("change", "change code / infra / cluster", "developer | platform-architect | gitops-operator",
           "dispatch the owning specialist with the goal and its acceptance",
           "a failing Node test: dispatch developer"),
    Intent("check", "check Gaia itself", "gaia-audit | gaia-check | gaia doctor",
           "gaia doctor",
           "gaia doctor --json"),
    Intent("reflect", "reflect / compact", "Skill session-reflection",
           "gaia session snapshot --session-id <id>; gaia session resume-point set",
           "gaia session snapshot --session-id <id> --json"),
)


def _ids(rows: list[dict], key: str, prefix: str = "") -> str:
    shown = ", ".join(f"{prefix}{row[key]}" for row in rows[:LISTED_IDS])
    return shown + (f", +{len(rows) - LISTED_IDS} more" if len(rows) > LISTED_IDS else "")


def _due_lines(workspace: Optional[str]) -> list[tuple[str, str]]:
    from gaia.store.reader import list_unread_notifications

    due = list_unread_notifications(workspace=workspace)
    reports = [row for row in due if row["kind"] == "report"]
    timed = [row for row in due if row["kind"] != "report"]
    lines = []
    if timed:
        named = ", ".join(f"{row['kind']} #{row['id']}" for row in timed[:LISTED_IDS])
        lines.append((f"{len(timed)} reminder(s)/routine(s) due: {named}",
                      "gaia notifications show <id> ; gaia notifications ack <id> ; "
                      "gaia notifications snooze <id> --for 1d ; gaia notifications cancel <id>"
                      "   (Skill reminders)"))
    if reports:
        lines.append((f"{len(reports)} unread task report(s): {_ids(reports, 'id', '#')}",
                      "gaia notifications list --unread ; gaia notifications ack <id>"))
    return lines


def _proposal_lines() -> list[tuple[str, str]]:
    from gaia.store.reader import pending_proposal_refs

    rows = pending_proposal_refs()
    if not rows:
        return []
    return [(f"{len(rows)} proposal(s) waiting on your decision: {_ids(rows, 'id', '#')}",
             "gaia contract proposals list ; gaia contract proposals accept|dismiss <id>")]


def _dispatched_lines(session_id: str, caller_agent: str) -> list[tuple[str, str]]:
    """Turns the session dispatched and never closed; none for a dispatched caller.

    A dispatched caller shares its parent's session id and the CLI holds only
    its agent name, so its own in-flight row cannot be told from a sibling's.
    """
    from gaia.store.reader import dispatched_handoffs

    if caller_agent:
        return []
    rows = dispatched_handoffs(session_id)
    if not rows:
        return []
    return [(f"{len(rows)} turn(s) dispatched and never closed: {_ids(rows, 'contract_id')}",
             f"gaia contract list --state DISPATCHED --session {session_id}")]


def _signal_lines(session_id: str) -> list[tuple[str, str]]:
    from gaia.signals import read_cursor, subject_label
    from gaia.store.reader import signals_from_others

    cursor = read_cursor(session_id) if session_id else None
    if cursor is None:
        return []
    rows = signals_from_others(session_id, cursor["last_id"])
    if not rows:
        return []
    counts: dict[str, int] = {}
    for row in rows:
        label = subject_label(row["subject"])
        counts[label] = counts.get(label, 0) + 1
    labels = ", ".join(f"{n} {label}" for label, n in counts.items())
    return [(f"{len(rows)} unread signal(s): {labels}", f"gaia signals --since {cursor['last_id']}")]


def _plan_lines(workspace: Optional[str]) -> list[tuple[str, str]]:
    from gaia.store.reader import active_plan_heads

    lines = []
    for head in active_plan_heads(workspace):
        if head["task_id"] is None:
            lines.append((f"plan {head['plan_id']} ({head['brief']}) active, no pending task",
                          f"gaia plan show {head['brief']}"))
        else:
            lines.append((f"plan {head['plan_id']} ({head['brief']}) active, next task "
                          f"{head['task_id']} (#{head['task_order']})",
                          f"gaia plan show {head['brief']} ; gaia task show --id {head['task_id']}"))
    return lines


def _approval_lines(session_id: str) -> list[tuple[str, str]]:
    """This session's live signature requests; another session's consent is not this one's to give."""
    if not session_id:
        return []
    from gaia.approvals import reading, store

    rows = store.list_pending(session_id=session_id)
    readings = reading.read_ids((row["id"] for row in rows), live=reading.live_sessions())
    for row in rows:
        row["state"] = readings.get(row["id"], {}).get("state")
    live, _ = reading.session_scope(rows, session_id)
    if not live:
        return []
    return [(f"{len(live)} signature(s) waiting in this session: {_ids(live, 'id')}",
             "gaia approvals pending ; gaia approvals show <id>")]


def collect_now(session_id: str, workspace: Optional[str], caller_agent: str = "") -> list[tuple[str, str]]:
    """Every live-state line as (what, verb that moves it); sources needing a session yield nothing without one."""
    return [
        *_due_lines(workspace),
        *_proposal_lines(),
        *_approval_lines(session_id),
        *_dispatched_lines(session_id, caller_agent),
        *_signal_lines(session_id),
        *_plan_lines(workspace),
    ]


def _table(rows: list[tuple[str, ...]], indent: str = "  ") -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return [indent + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)) + "  " + row[-1]
            for row in rows]


def render_map(now: list[tuple[str, str]], workspace: Optional[str]) -> str:
    """The whole map; the NOW section is omitted when nothing has live state."""
    lines = [f"Gaia map -- workspace {workspace or '(none)'}", ""]
    if now:
        lines.append("NOW -- live state with the verb that moves it")
        for what, verb in now:
            lines += [f"  {what}", f"      -> {verb}"]
        lines.append("")
    lines.append("HABITS -- do these unasked, report in one line")
    lines += _table([(event, f"-> {act}") for event, act in HABITS])
    lines += ["", "YOU WANT -> USE                 (gaia map <intent> for the exact command)"]
    lines += _table([(i.key, i.want, i.use) for i in INTENTS])
    lines += ["", "All verbs by lane: gaia --help   One intent in depth: gaia map remember"]
    return "\n".join(lines)


def render_intent(intent: Intent) -> str:
    """One intent expanded: what it serves, the capability, the exact command and one example."""
    return "\n".join([
        f"{intent.key} -- {intent.want}",
        f"  use:      {intent.use}",
        f"  command:  {intent.command}",
        f"  example:  {intent.example}",
    ])


def register(subparsers) -> None:
    """Register the `map` subcommand with the root parser."""
    p = subparsers.add_parser(
        "map",
        help="What is live now with the verb that moves it, the habits, and which capability serves an intent",
        description=(
            "Computed from the database and read-only. NOW lists only what has live state "
            "(due reminders and routines, pending proposals, turns dispatched and never "
            "closed, unread signals for this session, active plans with their next task); "
            "an empty NOW is omitted. HABITS and YOU WANT -> USE follow. Run on demand: it "
            "is not a signal channel."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  gaia map\n  gaia map remember\n",
    )
    p.add_argument("intent", nargs="?", default=None,
                   help=f"Expand one row: {', '.join(i.key for i in INTENTS)}.")


def cmd_map(args) -> int:
    """Dispatch handler for `gaia map`."""
    if args.intent is not None:
        match = next((i for i in INTENTS if i.key == args.intent), None)
        if match is None:
            print(f"Unknown intent {args.intent!r}; one of: {', '.join(i.key for i in INTENTS)}",
                  file=sys.stderr)
            return 2
        print(render_intent(match))
        return 0

    from gaia.agent_identity import dispatch_agent_from_env, host_session_from_env
    from gaia.project import cli_workspace

    workspace = cli_workspace(None)
    now = collect_now(host_session_from_env(), workspace, dispatch_agent_from_env())
    print(render_map(now, workspace))
    return 0
