"""gaia usage -- token usage per session, per agent, and per plan.

`usage show` reads the token_usage table; `usage ingest` fills it from Claude
Code transcripts. The engine lives in gaia/usage.py.
"""

import argparse
import json
import sys
from pathlib import Path

_SOURCE = (
    "Source: the token_usage table, one row per API message (session_id, "
    "message_id) taken from Claude Code transcripts (~/.claude/projects: the "
    "session's main <session>.jsonl and <session>/subagents/agent-<id>.jsonl, "
    "agent type from agent-<id>.meta.json) by `gaia usage ingest`. Counts are "
    "exact per message: input, output, cache write, cache read."
)

_SHOW_DESCRIPTION = f"""\
Token usage of a plan (--plan) or of a session (--session).

--plan N: every session in which a subagent bound to plan N ran, restricted to
the plan's window (created_at until closure; --since/--until override it), by
session and by agent type, each split three ways:
  bound    subagents whose contract row is bound to the plan (plan_task_id in
           one of its tasks, plan_id, or a verifier whose parent row is bound).
           Binding goes through agent_contract_handoffs.harness_agent_id.
  unbound  other subagents in the same sessions and window (planner, AC edits,
           side topics...).
  main     the orchestrator thread. It mixes topics, so for a plan it is an
           UPPER BOUND, not the plan's cost.

{_SOURCE}

NOT covered: sessions never ingested (run `gaia usage ingest`), and binding of
subagents whose contract row has no harness_agent_id. This is not the same
figure as `gaia metrics` token_usage, which sums episodes per subagent stop
and double counts (see that command).
"""


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "-"


def _print_table(title: str, rows: list, keys: tuple) -> None:
    print(title)
    head = [*keys, "subagents", "calls", "input", "output", "cache_write", "cache_read"]
    print("  " + " | ".join(head))
    for r in rows:
        cells = [str(r.get(k) or "-") for k in keys]
        cells += [_fmt(r["subagents"]), _fmt(r["calls"]), _fmt(r["input_tokens"]),
                  _fmt(r["output_tokens"]), _fmt(r["cache_creation_tokens"]),
                  _fmt(r["cache_read_tokens"])]
        print("  " + " | ".join(cells))


def _cmd_show(args) -> int:
    from gaia import usage
    from gaia.store.writer import _connect

    if (args.plan is None) == (args.session is None):
        print("gaia usage show: pass exactly one of --plan or --session", file=sys.stderr)
        return 2
    con = _connect()
    try:
        if args.plan is not None:
            report = usage.plan_report(con, args.plan, args.since, args.until)
            if report is None:
                print(f"gaia usage show: plan {args.plan} not found", file=sys.stderr)
                return 1
        else:
            report = usage.session_report(con, args.session)
    finally:
        con.close()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0
    if args.plan is not None:
        p = report["plan"]
        print(f"plan {p['plan_id']} ({p['status']}) brief {p['brief']} workspace {p['workspace']}")
        print(f"window {p['since']} .. {p['until'] or 'open'}  "
              f"bound subagents: {report['bound_agents_with_usage']} with usage "
              f"of {report['bound_agents_known']} bound")
        _print_table("by session", report["by_session"], ("session_id", "binding"))
        _print_table("by agent", report["by_agent"], ("agent_type", "binding"))
    else:
        print(f"session {report['session_id']}")
        _print_table("by agent", report["by_agent"], ("agent_type",))
    if report["total"]:
        _print_table("total", [report["total"]], ())
    else:
        print("no usage rows -- has the session been ingested? (gaia usage ingest)")
    return 0


def _cmd_ingest(args) -> int:
    from gaia import usage
    from gaia.store.writer import _connect

    projects_dir = Path(args.projects_dir).expanduser()
    if not projects_dir.is_dir():
        print(f"gaia usage ingest: {projects_dir} is not a directory", file=sys.stderr)
        return 1
    con = _connect()
    try:
        result = usage.ingest(con, usage.discover_transcripts(projects_dir, args.session))
    finally:
        con.close()
    if args.json:
        print(json.dumps(result))
    else:
        print(f"files={result['files']} messages={result['messages']} new_rows={result['new_rows']}")
    return 0


def register(subparsers) -> None:
    """Register the `usage` subcommand with the root parser."""
    parser = subparsers.add_parser(
        "usage",
        help="Token usage per session, agent and plan (from Claude Code transcripts)",
        description=_SOURCE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = parser.add_subparsers(dest="usage_action", metavar="<action>")

    show = actions.add_parser(
        "show",
        help="Tokens of a plan (bound / unbound / main) or of a session (read-only)",
        description=_SHOW_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia usage show --plan 77\n"
            "  gaia usage show --plan 77 --until 2026-09-25T03:46:41Z --json\n"
            "  gaia usage show --session 3784bdcf-416a-490d-8fbb-ffdb226e816e\n"
        ),
    )
    show.add_argument("--plan", type=int, default=None, metavar="ID", help="Plan id.")
    show.add_argument("--session", default=None, metavar="ID", help="Claude Code session id.")
    show.add_argument("--since", default=None, metavar="ISO", help="Window start (plan mode).")
    show.add_argument("--until", default=None, metavar="ISO", help="Window end (plan mode).")
    show.add_argument("--json", action="store_true", default=False, help="Emit JSON.")

    ingest = actions.add_parser(
        "ingest",
        help="Load Claude Code transcripts into token_usage (idempotent, writes the DB)",
        description=(
            "Reads every session transcript under --projects-dir (main thread and "
            "subagents) and upserts one row per API message. Idempotent: a message "
            "already stored is never added again, so running it twice reports "
            "new_rows=0 the second time, and re-running it later adds only the "
            "messages written since. NOT covered: transcripts Claude Code has "
            "already deleted (it prunes old sessions)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  gaia usage ingest\n"
            "  gaia usage ingest --session 3784bdcf-416a-490d-8fbb-ffdb226e816e\n"
        ),
    )
    ingest.add_argument("--projects-dir", default="~/.claude/projects", metavar="DIR",
                        help="Claude Code projects directory (default: ~/.claude/projects).")
    ingest.add_argument("--session", default=None, metavar="ID",
                        help="Only this session's transcripts.")
    ingest.add_argument("--json", action="store_true", default=False, help="Emit JSON.")


def cmd_usage(args) -> int:
    """Dispatch handler for `gaia usage`."""
    handlers = {"show": _cmd_show, "ingest": _cmd_ingest}
    handler = handlers.get(getattr(args, "usage_action", None))
    if handler is None:
        print("Usage: gaia usage <show|ingest>", file=sys.stderr)
        return 2
    return handler(args)
