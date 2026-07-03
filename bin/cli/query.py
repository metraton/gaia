"""
gaia query -- cross-surface analytical query.

A single read-only verb that filters across three substrate tables in one
call: curated ``memory``, ``episodes``, and the append-only
``harness_events`` mirror.

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


def _render_grouped_count(rows: list[dict], group_by: str | None) -> None:
    """Render the output of ``group_and_count`` as a small table."""
    if not rows:
        print("(no results)")
        return
    if not group_by:
        # Single total
        print(rows[0]["count"])
        return
    key_w = max(len(group_by.upper()),
                max(len(str(r.get(group_by) or "")) for r in rows))
    print(f"{group_by.upper():<{key_w}}  COUNT")
    print("-" * (key_w + 7))
    for r in rows:
        print(f"{(str(r.get(group_by) or '')):<{key_w}}  {r['count']}")


def _fmt_int(n) -> str:
    return "-" if n is None else f"{int(n):,}"


def _render_metrics_agg(rows: list[dict], group_by: str | None) -> None:
    """Render aggregate_metrics() output: avg compliance + token sums per group."""
    if not rows:
        print("(no results)")
        return
    key = group_by or "group"
    key_vals = [str(r.get(group_by) or "(all)") if group_by else "(all)" for r in rows]
    key_w = max(len(key.upper()), max(len(v) for v in key_vals))
    header = (f"{key.upper():<{key_w}}  {'COUNT':>6}  {'AVG_COMPL':>9}  "
              f"{'AVG_MS':>8}  {'IN_TOK':>12}  {'OUT_TOK':>12}  {'CACHE_RD':>14}")
    print(header)
    print("-" * len(header))
    for r, kv in zip(rows, key_vals):
        avg_c = "-" if r.get("avg_compliance_score") is None else f"{r['avg_compliance_score']:.1f}"
        avg_ms = _fmt_int(r.get("avg_duration_ms"))
        print(
            f"{kv:<{key_w}}  {r['count']:>6}  {avg_c:>9}  {avg_ms:>8}  "
            f"{_fmt_int(r.get('input_tokens')):>12}  "
            f"{_fmt_int(r.get('output_tokens_real')):>12}  "
            f"{_fmt_int(r.get('cache_read_tokens')):>14}"
        )


def _render_metrics_rows(rows: list[dict]) -> None:
    """Render per-episode --metrics rows (agent, compliance, tokens, model)."""
    if not rows:
        print("(no results)")
        return
    agent_w = max(len("AGENT"), max(len(r.get("agent") or "") for r in rows))
    header = (f"{'TIMESTAMP':<19}  {'AGENT':<{agent_w}}  {'COMPL':>5}  "
              f"{'GR':>2}  {'IN_TOK':>10}  {'OUT_REAL':>10}  MODEL")
    print(header)
    print("-" * len(header))
    for r in rows:
        raw = r.get("raw") or {}
        ts = (r.get("timestamp") or "")[:19]
        cs = raw.get("compliance_score")
        cs_s = "-" if cs is None else str(cs)
        grade = raw.get("compliance_grade") or "-"
        print(
            f"{ts:<19}  {(r.get('agent') or ''):<{agent_w}}  {cs_s:>5}  "
            f"{grade:>2}  {_fmt_int(raw.get('input_tokens')):>10}  "
            f"{_fmt_int(raw.get('output_tokens_real')):>10}  "
            f"{raw.get('model_used') or '-'}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def cmd_query(args) -> int:
    """Dispatcher for ``gaia query``."""
    from gaia.store.reader import (
        cross_surface_query,
        group_and_count,
        aggregate_metrics,
        _extract_text_needle,
        _highlight_snippet,
        _row_text_for_snippet,
    )

    workspace = _resolve_workspace(getattr(args, "workspace", None))
    surface = getattr(args, "surface", None) or "all"
    last = getattr(args, "last", 20)
    fmt = getattr(args, "format", None) or "table"
    as_json = getattr(args, "json", False) or fmt == "json"
    group_by = getattr(args, "group_by", None)
    do_count = bool(getattr(args, "count", False))
    do_snippets = bool(getattr(args, "snippets", False))
    do_metrics = bool(getattr(args, "metrics", False))

    # --metrics projects context_metrics telemetry, which only lives on the
    # episodes surface -- scope the query to it so the projection applies.
    if do_metrics:
        surface = "episodes"

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
            metrics=do_metrics,
        )
    except ValueError as exc:
        return _err(str(exc), as_json=as_json)

    # --metrics: project + optionally aggregate the per-turn telemetry.
    if do_metrics:
        if do_count or group_by:
            try:
                agg = aggregate_metrics(rows, group_by=group_by)
            except ValueError as exc:
                return _err(str(exc), as_json=as_json)
            if as_json or fmt == "json":
                print(json.dumps(agg, indent=2, default=str))
                return 0
            _render_metrics_agg(agg, group_by)
            return 0
        if as_json or fmt == "json":
            print(json.dumps(rows, indent=2, default=str))
            return 0
        if fmt == "count":
            print(len(rows))
            return 0
        _render_metrics_rows(rows)
        return 0

    # --snippets: rewrite the summary field with highlighted fragments.
    # Applies only when there is a textual filter (command_like / type / agent).
    if do_snippets:
        needle = _extract_text_needle(
            type_filter=getattr(args, "type", None),
            agent_filter=getattr(args, "agent", None),
            command_like=getattr(args, "command_like", None),
        )
        if needle:
            for r in rows:
                text = _row_text_for_snippet(r)
                snippet = _highlight_snippet(text, needle)
                if snippet:
                    r["summary"] = snippet

    # --count or --group-by: aggregate before rendering.
    if do_count or group_by:
        try:
            grouped = group_and_count(rows, group_by=group_by)
        except ValueError as exc:
            return _err(str(exc), as_json=as_json)
        if as_json or fmt == "json":
            print(json.dumps(grouped, indent=2, default=str))
            return 0
        _render_grouped_count(grouped, group_by)
        return 0

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
  gaia query --since=24h --failed
  gaia query --since=7d --command-like='%git push%' --group-by=day
  gaia query --metrics --since=7d --group-by=agent
  gaia query --metrics --agent=developer --since=24h
"""


def register(subparsers) -> None:
    """Register the ``query`` subcommand."""
    p = subparsers.add_parser(
        "query",
        help="Cross-surface read-only query (memory, episodes, harness_events)",
        description=(
            "Filter and merge rows across the three Gaia substrate surfaces. "
            "Supports time/agent/type filters, snippet highlighting, and "
            "group-by aggregation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_QUERY_EPILOG,
    )
    p.add_argument(
        "--surface", default="all",
        choices=("memory", "episodes", "harness_events", "all"),
        help="Surface to query. Default: all.",
    )
    p.add_argument(
        "--workspace", default=None,
        help="Workspace identity. Default: gaia.project.current() or 'me'.",
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
        "--last", type=int, default=20, metavar="N",
        help="Per-surface row cap. int. Default: 20.",
    )
    p.add_argument(
        "--agent", default=None, metavar="NAME",
        help="Filter by agent name (episodes, harness_events).",
    )
    p.add_argument(
        "--type", default=None, metavar="VALUE",
        help="Filter by type column.",
    )
    p.add_argument(
        "--command-like", dest="command_like", default=None, metavar="LIKE",
        help="SQL LIKE pattern matched against harness_events.result.",
    )
    p.add_argument(
        "--failed", action="store_true", default=False,
        help="Restrict to failure rows. bool. Default: false.",
    )
    p.add_argument(
        "--group-by", dest="group_by", default=None,
        choices=("surface", "agent", "type", "day"),
        help="Aggregate rows. 'day' truncates timestamp to YYYY-MM-DD.",
    )
    p.add_argument(
        "--count", action="store_true", default=False,
        help="Emit count instead of rows. Combine with --group-by for buckets.",
    )
    p.add_argument(
        "--snippets", action="store_true", default=False,
        help="Replace summary with [bracketed] fragments around the textual "
             "filter. No-op without --command-like / --type / --agent.",
    )
    p.add_argument(
        "--metrics", action="store_true", default=False,
        help="Project per-turn telemetry from episodes.context_metrics "
             "(compliance_score, token counts, duration_ms, tool/api calls, "
             "model). Forces --surface=episodes. Combine with --group-by / "
             "--count for avg-compliance + token-sum rollups per agent/day/type.",
    )
    p.add_argument(
        "--format", default="table",
        choices=("table", "json", "count"),
        help="Output shape. Default: table.",
    )
    p.add_argument(
        "--json", action="store_true", default=False,
        help="Alias for --format=json.",
    )
