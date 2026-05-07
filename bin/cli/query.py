"""
gaia query -- cross-surface analytical query.

A single read-only verb that filters across three substrate tables in one
call: curated ``memory``, ``episodes``, and the append-only
``harness_events`` mirror. Designed for diagnostic / forensic questions
that span more than one surface (e.g. "what failed in the last 24h?",
"which git pushes ran today?", "what did the developer agent emit?").

Output shape (always the same five columns regardless of surface):

    surface     -- 'memory' | 'episodes' | 'harness_events'
    timestamp   -- ISO8601 best-effort (memory.updated_at,
                   episodes.timestamp, harness_events.ts)
    type        -- surface-specific category
    agent       -- agent name when known (episodes / harness_events)
    summary     -- short human line derived from the source row

JSON output preserves the same shape plus the original row under ``raw``.
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


def _resolve_workspace(explicit: str | None) -> str | None:
    """Resolve workspace; ``None`` means 'no workspace filter'."""
    if explicit:
        return explicit
    try:
        from gaia.project import current as _project_current
        ws = _project_current()
        if ws and ws != "global":
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
# Output renderers
# ---------------------------------------------------------------------------

def _render_table(rows: list[dict]) -> None:
    if not rows:
        print("(no results)")
        return
    # column widths
    surf_w = max(len("SURFACE"), max(len(r.get("surface", "")) for r in rows))
    ts_w = max(len("TIMESTAMP"), max(len((r.get("timestamp") or "")[:19])
                                     for r in rows))
    type_w = max(len("TYPE"), max(len(r.get("type") or "") for r in rows))
    agent_w = max(len("AGENT"),
                  max(len(r.get("agent") or "") for r in rows))
    # cap summary to the remaining viewport (~120 chars total)
    summary_max = max(20, 120 - (surf_w + ts_w + type_w + agent_w + 4 * 2))

    header = (f"{'SURFACE':<{surf_w}}  {'TIMESTAMP':<{ts_w}}  "
              f"{'TYPE':<{type_w}}  {'AGENT':<{agent_w}}  SUMMARY")
    print(header)
    print("-" * len(header))
    for r in rows:
        summary = r.get("summary") or ""
        if len(summary) > summary_max:
            summary = summary[: summary_max - 3] + "..."
        ts = (r.get("timestamp") or "")[:19]
        print(
            f"{r.get('surface', ''):<{surf_w}}  "
            f"{ts:<{ts_w}}  "
            f"{(r.get('type') or ''):<{type_w}}  "
            f"{(r.get('agent') or ''):<{agent_w}}  "
            f"{summary}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def cmd_query(args) -> int:
    """Dispatcher for ``gaia query``."""
    from gaia.store.reader import cross_surface_query

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    surface = getattr(args, "surface", None) or "all"
    last = getattr(args, "last", 20)
    fmt = getattr(args, "format", None) or "table"
    as_json = getattr(args, "json", False) or fmt == "json"

    try:
        rows = cross_surface_query(
            surface=surface,
            workspace=workspace,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            last=last,
            type=getattr(args, "type", None),
            agent=getattr(args, "agent", None),
            command_like=getattr(args, "command_like", None),
            failed=getattr(args, "failed", False),
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if fmt == "count":
        print(len(rows))
        return 0
    if as_json or fmt == "json":
        print(json.dumps(rows, indent=2, default=str))
        return 0

    _render_table(rows)
    return 0


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

_QUERY_EPILOG = """\
Examples:
  Last 20 events across every surface in the current workspace:
    gaia query

  Failures in the last 24 hours (episodes blocked / harness errors):
    gaia query --since=24h --failed

  Every git-push command captured by the harness in the last week:
    gaia query --surface=harness_events --command-like='%git push%' --since=7d

  Recent activity for the developer agent in the last 7 days:
    gaia query --since=7d --agent=developer --last=50

  Count rows that match (no row text):
    gaia query --since=24h --failed --format=count

  Restrict to one surface only:
    gaia query --surface=episodes --type=task --since=2026-05-01
"""


def register(subparsers) -> None:
    """Register the ``query`` subcommand."""
    p = subparsers.add_parser(
        "query",
        help="Cross-surface analytical query (memory + episodes + "
             "harness_events)",
        description=(
            "Run a single read-only query that filters across all three "
            "Gaia substrate surfaces (curated memory, episodic memory, "
            "harness events) and merges results into one normalized table. "
            "Useful for forensic questions that span more than one surface."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_QUERY_EPILOG,
    )
    p.add_argument(
        "--surface", default="all",
        choices=("memory", "episodes", "harness_events", "all"),
        help="Which substrate table(s) to query (default: all)",
    )
    p.add_argument(
        "--workspace", default=None,
        help="Workspace identity to filter by "
             "(default: gaia.project.current() or 'me')",
    )
    p.add_argument(
        "--since", default=None, metavar="DURATION_OR_DATE",
        help="Lower-bound timestamp filter. Accepts a duration "
             "('24h', '7d', '30m', '2w') interpreted as 'now minus N', "
             "or an ISO date ('2026-05-01') / datetime "
             "('2026-05-01T10:00:00')",
    )
    p.add_argument(
        "--until", default=None, metavar="DURATION_OR_DATE",
        help="Upper-bound timestamp filter (same format as --since)",
    )
    p.add_argument(
        "--last", type=int, default=20, metavar="N",
        help="Per-surface row cap (default: 20). Total rows returned "
             "can be up to N x number of surfaces",
    )
    p.add_argument(
        "--agent", default=None, metavar="NAME",
        help="Filter by agent name (applies to episodes and harness_events; "
             "memory surface has no agent column)",
    )
    p.add_argument(
        "--type", default=None, metavar="VALUE",
        help="Filter by type column "
             "(memory.type / episodes.type / harness_events.type)",
    )
    p.add_argument(
        "--command-like", dest="command_like", default=None, metavar="LIKE",
        help="SQL LIKE pattern matched against harness_events.result "
             "(where command.executed events store the command line). "
             "Use '%%' as wildcard, e.g. \"--command-like='%%git push%%'\". "
             "Ignored for memory and episodes surfaces",
    )
    p.add_argument(
        "--failed", action="store_true", default=False,
        help="Restrict to failure rows: episodes with plan_status BLOCKED or "
             "NEEDS_INPUT (or outcome != success), and harness_events with "
             "severity=error or result starting with fail/error. "
             "Memory rows have no failure concept and are excluded when set",
    )
    p.add_argument(
        "--format", default="table",
        choices=("table", "json", "count"),
        help="Output shape (default: table). 'json' emits one array of "
             "objects with the original row under 'raw'; 'count' emits the "
             "single integer row count",
    )
    p.add_argument(
        "--json", action="store_true", default=False,
        help="Alias for --format=json",
    )
