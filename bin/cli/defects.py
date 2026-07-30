"""
gaia defects -- row-level defect triage.

``gaia metrics`` answers how MANY defects of each type exist. This verb
answers WHICH ones: one line per defect, never an aggregate, so a class of
defect can be isolated and its instances read one by one.

Rows come from the two channels a defect can reach the substrate through and
are merged into a single listing (see ``gaia.store.reader.read_defects``):

    subagent      -- episode_anomalies, the raw defect floor
    orchestrator  -- harness_events graded above ``info``, the failures
                     observed from outside a subagent turn

Output columns are the same for both origins:

    origin     -- 'subagent' | 'orchestrator'
    date       -- ISO8601 timestamp, second precision
    type       -- defect type ('skipped_verification', 'agent.cut',
                  'agent.contract_rejected', ...)
    severity   -- 'info' | 'warning' | 'error' | 'critical'
    agent      -- agent the defect is attributed to, when known
    message    -- one-line description from the source row

JSON output preserves the same shape plus ``id``, ``workspace`` and
``source`` (the parent episode_id / event source).
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
    if explicit == "all":
        return None
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


def _render_table(rows: list[dict], workspace: str | None = None) -> None:
    if not rows:
        # Name the workspace the filter resolved to. An empty listing is
        # otherwise indistinguishable from "no defects anywhere", and the
        # default resolves to the cwd's workspace, which is frequently not
        # the one the defects were recorded under.
        scope = f"workspace={workspace}, try --workspace=all" if workspace else "all workspaces"
        print(f"(no defects -- {scope})")
        return

    origin_w = max(len("ORIGIN"), max(len(r.get("origin") or "") for r in rows))
    type_w = max(len("TYPE"), max(len(r.get("type") or "") for r in rows))
    sev_w = max(len("SEVERITY"), max(len(r.get("severity") or "") for r in rows))
    agent_w = max(len("AGENT"), max(len(r.get("agent") or "-") for r in rows))
    # Cap the message to whatever the ~140-char viewport has left.
    msg_max = max(20, 140 - (origin_w + 19 + type_w + sev_w + agent_w + 5 * 2))

    header = (f"{'ORIGIN':<{origin_w}}  {'DATE':<19}  {'TYPE':<{type_w}}  "
              f"{'SEVERITY':<{sev_w}}  {'AGENT':<{agent_w}}  MESSAGE")
    print(header)
    print("-" * len(header))
    for r in rows:
        message = r.get("message") or ""
        if len(message) > msg_max:
            message = message[: msg_max - 3] + "..."
        print(
            f"{(r.get('origin') or ''):<{origin_w}}  "
            f"{(r.get('timestamp') or '')[:19]:<19}  "
            f"{(r.get('type') or ''):<{type_w}}  "
            f"{(r.get('severity') or ''):<{sev_w}}  "
            f"{(r.get('agent') or '-'):<{agent_w}}  "
            f"{message}"
        )


def cmd_defects(args) -> int:
    """Handler for ``gaia defects``."""
    from gaia.store.reader import read_defects

    as_json = bool(getattr(args, "json", False))
    workspace = _resolve_workspace(getattr(args, "workspace", None))

    try:
        rows = read_defects(
            origin=getattr(args, "origin", None) or "all",
            workspace=workspace,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            type=getattr(args, "type", None),
            severity=getattr(args, "severity", None),
            agent=getattr(args, "agent", None),
            limit=getattr(args, "limit", 20),
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    if getattr(args, "count", False):
        print(json.dumps({"count": len(rows)}) if as_json else len(rows))
        return 0
    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    _render_table(rows, workspace)
    return 0


_DEFECTS_EPILOG = """\
Examples:
  gaia defects                                     # newest defects, both origins
  gaia defects --type=skipped_verification         # every instance of one class
  gaia defects --type=agent.contract_rejected      # rejected handoff contracts
  gaia defects --origin=orchestrator --since=7d    # harness-observed failures
  gaia defects --severity=critical --limit=50
  gaia defects --agent=gaia-system --json

Per-type counts are `gaia metrics`; this verb is the row-level complement.
"""


def register(subparsers) -> None:
    """Register the ``defects`` subcommand."""
    p = subparsers.add_parser(
        "defects",
        help="List individual defects for triage (row-level, not aggregated)",
        description=(
            "List individual defect rows across the subagent defect floor "
            "(episode_anomalies) and orchestrator-origin harness observations "
            "(harness_events above info severity), with triage filters."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DEFECTS_EPILOG,
    )
    p.add_argument(
        "--origin", default="all",
        choices=("subagent", "orchestrator", "all"),
        help="Defect channel. Default: all.",
    )
    p.add_argument(
        "--type", default=None, metavar="VALUE",
        help=(
            "Exact defect type ('skipped_verification', 'agent.cut', "
            "'agent.contract_rejected', ...)."
        ),
    )
    p.add_argument(
        "--severity", default=None, metavar="LEVEL",
        help="Exact severity, case-insensitive (info/warning/error/critical).",
    )
    p.add_argument(
        "--agent", default=None, metavar="NAME",
        help="Agent the defect is attributed to.",
    )
    p.add_argument(
        "--since", default=None, metavar="DUR_OR_DATE",
        help="Lower bound. Duration ('24h', '7d') or ISO date. Default: none.",
    )
    p.add_argument(
        "--until", default=None, metavar="DUR_OR_DATE",
        help="Upper bound. Same format as --since. Default: none.",
    )
    p.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="Per-origin row cap. int. Default: 20.",
    )
    p.add_argument(
        "--workspace", default=None, metavar="W",
        help="Workspace identity, or 'all' for every workspace. "
             "Default: gaia.project.current() or 'me'.",
    )
    p.add_argument(
        "--count", action="store_true", default=False,
        help="Print the number of matching defects instead of the rows.",
    )
    p.add_argument(
        "--json", action="store_true", default=False,
        help="Emit JSON rows (adds id, workspace, source).",
    )
