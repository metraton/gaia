"""
gaia.store.reader -- analytical / read-only cross-surface queries.

This module is the read-side complement to ``gaia.store.writer``. It exists
so that callers (notably ``gaia query``) can ask analytical questions across
multiple substrate tables without each CLI growing its own ad-hoc SQL.

Design:
  * Pure read-only -- no INSERT/UPDATE/DELETE here.
  * Cross-surface -- queries can mix curated ``memory`` rows, ``episodes``,
    and the append-only ``harness_events`` mirror in a single result set.
  * Filter-driven -- callers pass a ``filters`` dict; the function builds
    one SELECT per surface, UNIONs the results in Python (each surface has
    a different schema), and returns a list of normalized dicts that all
    share the same shape.

The unified output row shape is:

    {
        "surface":   "memory" | "episodes" | "harness_events",
        "timestamp": ISO8601 string (best-effort -- updated_at / ts / ...),
        "type":      surface-specific string (memory.type, episodes.type,
                     harness_events.type),
        "agent":     agent name when known, else None,
        "summary":   short human-readable line for table display,
        "raw":       the original row as a plain dict (kept for JSON output),
    }
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Connection helper -- reuse writer's _connect to inherit schema bootstrap
# ---------------------------------------------------------------------------

def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    from gaia.store.writer import _connect as _writer_connect
    return _writer_connect(db_path)


# ---------------------------------------------------------------------------
# task_notifications reads (headless scheduled-task reports)
# ---------------------------------------------------------------------------
#
# Read-side complement to the writer's add/ack API. Used by the `gaia
# notifications list|show` CLI and by the hooks (SessionStart list + per-prompt
# unread counter). All read-only (T0). ``workspace=None`` means "all workspaces"
# for the count/list helpers; the CLI scopes to the active workspace by default.

def count_unread_notifications(
    workspace: str | None = None,
    db_path: Path | None = None,
) -> int:
    """Return the number of unread task-notifications, optionally scoped.

    Fail-soft: returns 0 on any query/connection error so the per-prompt hook
    counter never breaks the pipeline.
    """
    try:
        con = _connect(db_path)
    except Exception:
        return 0
    try:
        if workspace is None:
            row = con.execute(
                "SELECT COUNT(*) FROM task_notifications WHERE unread = 1"
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) FROM task_notifications WHERE unread = 1 AND workspace = ?",
                (workspace,),
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        con.close()


def list_unread_notifications(
    workspace: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return unread task-notifications, newest first, as plain dicts.

    Each dict carries: id, workspace, task_name, headline, body, session_id,
    created_at, unread, acked_at. Fail-soft: returns [] on any error.
    """
    try:
        con = _connect(db_path)
    except Exception:
        return []
    try:
        if workspace is None:
            rows = con.execute(
                "SELECT * FROM task_notifications WHERE unread = 1 "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM task_notifications WHERE unread = 1 AND workspace = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (workspace, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


def get_notification(
    notification_id: int,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return one task-notification by id as a plain dict, or None.

    Read-only; does NOT change the unread flag (that is `ack`'s job).
    """
    try:
        con = _connect(db_path)
    except Exception:
        return None
    try:
        row = con.execute(
            "SELECT * FROM task_notifications WHERE id = ?",
            (notification_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# scheduled_tasks reads (OS-agnostic desired state)
# ---------------------------------------------------------------------------
#
# Read-side complement to the writer's upsert/enable/delete API. Used by the
# `gaia schedule list|show|status` CLI, by `gaia schedule sync`, and by the
# SessionStart reconciliation block. All read-only (T0). Each task dict includes
# a parsed ``schedule_spec`` (dict) under ``spec`` and, for named-scope tasks,
# the ``machines`` list.

def _row_to_task(con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    task = dict(row)
    try:
        task["spec"] = json.loads(task.get("schedule_spec") or "{}")
    except Exception:
        task["spec"] = {}
    if task.get("machine_scope") == "named":
        ms = con.execute(
            "SELECT machine_name FROM scheduled_task_machines WHERE task_id = ? "
            "ORDER BY machine_name",
            (task["id"],),
        ).fetchall()
        task["machines"] = [r["machine_name"] for r in ms]
    else:
        task["machines"] = []
    return task


def list_scheduled_tasks(
    workspace: str | None = None,
    include_disabled: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return desired-state tasks (optionally workspace-scoped), newest first.

    Fail-soft: returns [] on any error so the CLI / hook never breaks.
    """
    try:
        con = _connect(db_path)
    except Exception:
        return []
    try:
        clauses = []
        params: list[Any] = []
        if workspace is not None:
            clauses.append("workspace IS ?")
            params.append(workspace)
        if not include_disabled:
            clauses.append("enabled = 1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = con.execute(
            f"SELECT * FROM scheduled_tasks{where} ORDER BY created_at DESC, id DESC",
            tuple(params),
        ).fetchall()
        return [_row_to_task(con, r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


def get_scheduled_task(
    name: str,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return one desired-state task by (workspace, name), or None."""
    try:
        con = _connect(db_path)
    except Exception:
        return None
    try:
        row = con.execute(
            "SELECT * FROM scheduled_tasks WHERE name = ? AND workspace IS ?",
            (name, workspace),
        ).fetchone()
        return _row_to_task(con, row) if row else None
    finally:
        con.close()


def scheduled_tasks_for_machine(
    machine_name: str,
    workspace: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return ENABLED desired-state tasks that apply to ``machine_name``.

    A task applies when machine_scope='all', or machine_scope='named' and
    ``machine_name`` is in its scheduled_task_machines. This is what a sync on a
    given machine, and the SessionStart reconciliation block, iterate over.
    Fail-soft: returns [] on any error.
    """
    try:
        con = _connect(db_path)
    except Exception:
        return []
    try:
        clauses = ["enabled = 1"]
        params: list[Any] = []
        if workspace is not None:
            clauses.append("workspace IS ?")
            params.append(workspace)
        where = " WHERE " + " AND ".join(clauses)
        rows = con.execute(
            f"SELECT * FROM scheduled_tasks{where} ORDER BY name",
            tuple(params),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            task = _row_to_task(con, r)
            if task.get("machine_scope") == "all" or machine_name in task.get("machines", []):
                out.append(task)
        return out
    except Exception:
        return []
    finally:
        con.close()


def get_scheduled_task_state(
    task_id: int,
    machine_name: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return per-machine materialization state for a task, or None."""
    try:
        con = _connect(db_path)
    except Exception:
        return None
    try:
        row = con.execute(
            "SELECT * FROM scheduled_task_state WHERE task_id = ? AND machine_name = ?",
            (task_id, machine_name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Duration / date parsing for --since / --until
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)


def parse_when(value: str) -> str:
    """Normalize a ``--since`` / ``--until`` value to an ISO8601 UTC string.

    Accepts:
      * Duration: ``"24h"``, ``"7d"``, ``"30m"``, ``"2w"``, ``"45s"``.
        Interpreted as "now minus N units" (so ``--since=24h`` means the
        last 24 hours).
      * Date-only:   ``"2026-05-01"`` -> ``2026-05-01T00:00:00Z``.
      * Datetime:    ``"2026-05-01T10:00:00"`` (Z optional).

    Raises:
        ValueError: when the input matches none of the above.
    """
    if not value:
        raise ValueError("empty time value")
    s = value.strip()

    m = _DURATION_RE.match(s)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        delta = {
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        anchor = datetime.now(tz=timezone.utc) - delta
        return anchor.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Date-only YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s}T00:00:00Z"

    # Datetime: try fromisoformat (allow trailing Z)
    iso = s.rstrip("Z")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(
            f"could not parse '{value}' as duration (e.g. '24h', '7d') "
            f"or date (YYYY-MM-DD / YYYY-MM-DDTHH:MM:SS)"
        ) from exc


# ---------------------------------------------------------------------------
# Per-surface query helpers
# ---------------------------------------------------------------------------

def _query_memory(
    con: sqlite3.Connection,
    *,
    workspace: str | None,
    since_iso: str | None,
    until_iso: str | None,
    type_filter: str | None,
    limit: int,
) -> list[dict]:
    where = []
    params: list[Any] = []
    if workspace:
        where.append("workspace = ?")
        params.append(workspace)
    if since_iso:
        where.append("COALESCE(updated_at, '') >= ?")
        params.append(since_iso)
    if until_iso:
        where.append("COALESCE(updated_at, '') <= ?")
        params.append(until_iso)
    if type_filter:
        where.append("type = ?")
        params.append(type_filter)

    # scan-v2 SV3: tombstoned rows (deleted_at non-NULL) are soft-deleted and
    # must never surface in a query. delete_memory() stamps deleted_at instead
    # of physically removing the row, so this filter is what keeps a tombstone
    # invisible to `gaia query` / cross_surface_query.
    where.append("deleted_at IS NULL")

    sql = (
        "SELECT workspace, name, type, description, body, origin_session_id, "
        "updated_at "
        "FROM memory"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(updated_at, '') DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        desc = (d.get("description") or "").strip()
        body = (d.get("body") or "").strip().replace("\n", " ")
        if len(body) > 80:
            body = body[:77] + "..."
        summary_parts = [d["name"]]
        if desc:
            summary_parts.append(f"-- {desc}")
        elif body:
            summary_parts.append(f"-- {body}")
        out.append({
            "surface": "memory",
            "timestamp": d.get("updated_at") or "",
            "type": d.get("type") or "",
            "agent": None,
            "summary": " ".join(summary_parts),
            "raw": d,
        })
    return out


# Metric fields projected out of episodes.context_metrics when metrics=True.
# The workflow recorder nests these under the "metrics" key of the JSON blob
# (T4 episodic-workflow-to-db); a few older migrated rows store them at the
# top level, so each extraction COALESCEs the nested path with the flat one.
# json1 is compiled into SQLite by default -- no schema migration required.
_METRIC_JSON_FIELDS = (
    # (output column, json leaf path under $.metrics / $)
    ("compliance_score", "compliance_score.total"),
    ("compliance_grade", "compliance_score.grade"),
    ("input_tokens", "input_tokens"),
    ("cache_creation_tokens", "cache_creation_tokens"),
    ("cache_read_tokens", "cache_read_tokens"),
    ("output_tokens_real", "output_tokens_real"),
    ("duration_ms", "duration_ms"),
    ("tool_call_count", "tool_call_count"),
    ("api_call_count", "api_call_count"),
    ("model_used", "model_used"),
)

# Numeric metric fields summed by aggregate_metrics.
_METRIC_SUM_FIELDS = (
    "input_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "output_tokens_real",
    "tool_call_count",
    "api_call_count",
)


def _metric_select_columns() -> str:
    """Build the json_extract projection clause for --metrics queries."""
    cols = []
    for out_col, leaf in _METRIC_JSON_FIELDS:
        cols.append(
            f"COALESCE("
            f"json_extract(context_metrics, '$.metrics.{leaf}'), "
            f"json_extract(context_metrics, '$.{leaf}')"
            f") AS {out_col}"
        )
    return ", ".join(cols)


def _query_episodes(
    con: sqlite3.Connection,
    *,
    workspace: str | None,
    since_iso: str | None,
    until_iso: str | None,
    type_filter: str | None,
    agent_filter: str | None,
    failed: bool,
    limit: int,
    metrics: bool = False,
) -> list[dict]:
    where = []
    params: list[Any] = []
    if workspace:
        where.append("workspace = ?")
        params.append(workspace)
    if since_iso:
        where.append("timestamp >= ?")
        params.append(since_iso)
    if until_iso:
        where.append("timestamp <= ?")
        params.append(until_iso)
    if type_filter:
        where.append("type = ?")
        params.append(type_filter)
    if agent_filter:
        where.append("agent = ?")
        params.append(agent_filter)
    if failed:
        # plan_status BLOCKED / NEEDS_INPUT or non-success outcome
        where.append(
            "(plan_status IN ('BLOCKED', 'NEEDS_INPUT') "
            "OR (outcome IS NOT NULL AND outcome NOT IN ('success', '')))"
        )
    if metrics:
        # Only rows that actually carry a metrics blob can project fields.
        where.append("context_metrics IS NOT NULL")

    base_cols = (
        "episode_id, workspace, timestamp, session_id, task_id, agent, "
        "type, title, plan_status, outcome, exit_code, duration_seconds"
    )
    if metrics:
        sql = f"SELECT {base_cols}, {_metric_select_columns()} FROM episodes"
    else:
        sql = f"SELECT {base_cols} FROM episodes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        title = (d.get("title") or "").strip()
        ps = d.get("plan_status") or ""
        oc = d.get("outcome") or ""
        bits = [title or d.get("episode_id", "")]
        if metrics:
            # Metrics view: lead with the projected metric fields.
            mtail = []
            cs = d.get("compliance_score")
            if cs is not None:
                grade = d.get("compliance_grade") or ""
                mtail.append(f"compliance={cs}{('/' + grade) if grade else ''}")
            it = d.get("input_tokens")
            ot = d.get("output_tokens_real")
            if it is not None or ot is not None:
                mtail.append(f"tok in={it if it is not None else '?'} out_real={ot if ot is not None else '?'}")
            model = d.get("model_used")
            if model:
                mtail.append(f"model={model}")
            if mtail:
                bits.append("[" + ", ".join(mtail) + "]")
        else:
            tail = []
            if ps:
                tail.append(f"plan_status={ps}")
            if oc and oc != ps:
                tail.append(f"outcome={oc}")
            if tail:
                bits.append("[" + ", ".join(tail) + "]")
        out.append({
            "surface": "episodes",
            "timestamp": d.get("timestamp") or "",
            "type": d.get("type") or "",
            "agent": d.get("agent"),
            "summary": " ".join(bits),
            "raw": d,
        })
    return out


def _query_harness_events(
    con: sqlite3.Connection,
    *,
    workspace: str | None,
    since_iso: str | None,
    until_iso: str | None,
    type_filter: str | None,
    agent_filter: str | None,
    command_like: str | None,
    failed: bool,
    limit: int,
) -> list[dict]:
    where = []
    params: list[Any] = []
    if workspace:
        where.append("(workspace = ? OR workspace IS NULL)")
        params.append(workspace)
    if since_iso:
        where.append("ts >= ?")
        params.append(since_iso)
    if until_iso:
        where.append("ts <= ?")
        params.append(until_iso)
    if type_filter:
        where.append("type = ?")
        params.append(type_filter)
    if agent_filter:
        where.append("agent = ?")
        params.append(agent_filter)
    if command_like:
        # The command line is captured in the `result` field for
        # command.executed events (e.g. "ok: git push ..."). Filter via
        # SQL LIKE on result.
        where.append("result LIKE ?")
        params.append(command_like)
    if failed:
        # For harness_events, "failed" maps to severity=error or
        # result-string starting with 'fail'/'error', plus payload exit_code != 0
        # when present. We use a SQL approximation here; payload exit_code
        # parsing happens in Python below if needed.
        where.append(
            "(severity = 'error' OR result LIKE 'fail%' OR result LIKE 'error%')"
        )

    sql = (
        "SELECT id, workspace, ts, type, source, agent, result, severity, payload "
        "FROM harness_events"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    rows = con.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        # Optional payload-level filtering: for command.executed, an exit_code
        # field may live inside the JSON payload. When --failed was requested
        # but the SQL approximation matched too broadly, keep the row as-is;
        # users can refine with --command-like or --type.
        result = (d.get("result") or "").strip().replace("\n", " ")
        if len(result) > 80:
            result = result[:77] + "..."
        bits = []
        sev = d.get("severity") or ""
        if sev and sev != "info":
            bits.append(f"({sev})")
        if result:
            bits.append(result)
        out.append({
            "surface": "harness_events",
            "timestamp": d.get("ts") or "",
            "type": d.get("type") or "",
            "agent": d.get("agent") or None,
            "summary": " ".join(bits) or f"id={d.get('id')}",
            "raw": d,
        })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

VALID_SURFACES = ("memory", "episodes", "harness_events", "all")
VALID_GROUP_BY = ("surface", "agent", "type", "day")


def _highlight_snippet(
    text: str,
    needle: str,
    *,
    radius: int = 60,
    max_snippets: int = 3,
) -> str:
    """Return a summary string with up to N fragments highlighting ``needle``.

    Pipe-safe: wraps matches with ``[..]`` brackets (no ANSI). Returns the
    original ``text`` (truncated) when the needle is empty or absent.
    """
    if not text:
        return ""
    if not needle:
        return text[:160] + ("..." if len(text) > 160 else "")

    flat = text.replace("\n", " ")
    needle_lc = needle.lower()
    flat_lc = flat.lower()
    pos = 0
    fragments: list[str] = []
    while len(fragments) < max_snippets:
        idx = flat_lc.find(needle_lc, pos)
        if idx < 0:
            break
        start = max(0, idx - radius)
        end = min(len(flat), idx + len(needle) + radius)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(flat) else ""
        slice_ = flat[start:idx] + "[" + flat[idx:idx + len(needle)] + "]" + flat[idx + len(needle):end]
        fragments.append(f"{prefix}{slice_}{suffix}")
        pos = idx + len(needle)
    if not fragments:
        return flat[:160] + ("..." if len(flat) > 160 else "")
    return " | ".join(fragments)


def _extract_text_needle(
    *,
    type_filter: str | None,
    agent_filter: str | None,
    command_like: str | None,
) -> str:
    """Pick the textual filter (if any) used to drive snippet highlighting.

    Priority: command_like (stripped of '%') > type_filter > agent_filter.
    Returns ``""`` when no textual filter applies.
    """
    if command_like:
        return command_like.replace("%", "").strip()
    if type_filter:
        return type_filter.strip()
    if agent_filter:
        return agent_filter.strip()
    return ""


def _row_text_for_snippet(row: dict) -> str:
    """Pick the textual field for snippet rendering by surface."""
    surface = row.get("surface")
    raw = row.get("raw") or {}
    if surface == "memory":
        body = raw.get("body") or ""
        desc = raw.get("description") or ""
        return f"{desc}\n{body}".strip() if (desc or body) else (row.get("summary") or "")
    if surface == "episodes":
        return raw.get("title") or row.get("summary") or ""
    if surface == "harness_events":
        return raw.get("result") or row.get("summary") or ""
    return row.get("summary") or ""


def _truncate_day(ts: str | None) -> str:
    """Return the YYYY-MM-DD prefix of an ISO timestamp, or '' if missing."""
    if not ts:
        return ""
    return ts[:10]


def group_and_count(
    rows: list[dict],
    *,
    group_by: str | None,
) -> list[dict]:
    """Aggregate rows into ``{group, count}`` pairs.

    When ``group_by`` is ``None`` the function returns a single
    ``[{"count": N}]`` row -- equivalent to ``--count`` without grouping.
    Group order is descending count, ties broken alphabetically.
    """
    if not group_by:
        return [{"count": len(rows)}]
    if group_by not in VALID_GROUP_BY:
        raise ValueError(
            f"invalid group_by '{group_by}'; must be one of {list(VALID_GROUP_BY)}"
        )

    buckets: dict[str, int] = {}
    for r in rows:
        if group_by == "day":
            key = _truncate_day(r.get("timestamp"))
        else:
            key = r.get(group_by) or ""
        buckets[key] = buckets.get(key, 0) + 1

    out = [{group_by: k, "count": v} for k, v in buckets.items()]
    out.sort(key=lambda d: (-d["count"], d.get(group_by) or ""))
    return out


def aggregate_metrics(
    rows: list[dict],
    *,
    group_by: str | None,
) -> list[dict]:
    """Roll up ``--metrics`` episode rows into per-group summaries.

    Each output bucket carries ``count``, ``avg_compliance_score`` (over rows
    that have one), ``avg_duration_ms``, and the sum of every field in
    ``_METRIC_SUM_FIELDS``. When ``group_by`` is ``None`` a single ``(all)``
    bucket is returned. Reuses the same ``VALID_GROUP_BY`` keys as
    :func:`group_and_count`; metric values are read from each row's ``raw``.
    Group order is descending count, ties broken alphabetically.
    """
    if group_by and group_by not in VALID_GROUP_BY:
        raise ValueError(
            f"invalid group_by '{group_by}'; must be one of {list(VALID_GROUP_BY)}"
        )

    buckets: dict[str, dict] = {}
    for r in rows:
        raw = r.get("raw") or {}
        if group_by is None:
            key = "(all)"
        elif group_by == "day":
            key = _truncate_day(r.get("timestamp"))
        elif group_by == "surface":
            key = r.get("surface") or ""
        else:
            key = r.get(group_by) or raw.get(group_by) or ""

        b = buckets.get(key)
        if b is None:
            b = {
                "count": 0,
                "compliance_sum": 0.0,
                "compliance_n": 0,
                "duration_sum": 0.0,
                "duration_n": 0,
            }
            for f in _METRIC_SUM_FIELDS:
                b[f] = 0
            buckets[key] = b

        b["count"] += 1
        cs = raw.get("compliance_score")
        if isinstance(cs, (int, float)):
            b["compliance_sum"] += cs
            b["compliance_n"] += 1
        dm = raw.get("duration_ms")
        if isinstance(dm, (int, float)):
            b["duration_sum"] += dm
            b["duration_n"] += 1
        for f in _METRIC_SUM_FIELDS:
            v = raw.get(f)
            if isinstance(v, (int, float)):
                b[f] += v

    out = []
    for key, b in buckets.items():
        row = {
            "count": b["count"],
            "avg_compliance_score": (
                round(b["compliance_sum"] / b["compliance_n"], 1)
                if b["compliance_n"] else None
            ),
            "avg_duration_ms": (
                round(b["duration_sum"] / b["duration_n"])
                if b["duration_n"] else None
            ),
        }
        for f in _METRIC_SUM_FIELDS:
            row[f] = b[f]
        if group_by:
            row[group_by] = key
        out.append(row)

    out.sort(key=lambda d: (-d["count"], str(d.get(group_by) if group_by else "")))
    return out


def cross_surface_query(
    *,
    surface: str = "all",
    workspace: str | None = None,
    since: str | None = None,
    until: str | None = None,
    last: int = 20,
    type: str | None = None,
    agent: str | None = None,
    command_like: str | None = None,
    failed: bool = False,
    metrics: bool = False,
    db_path: Path | None = None,
) -> list[dict]:
    """Run a cross-surface analytical query against the substrate.

    Each surface is queried independently with the filters that apply to it,
    then results are merged (newest first by ``timestamp``) and capped at
    ``last`` per surface (NOT globally -- callers wanting a global cap can
    slice the returned list).

    Args:
        surface:       ``memory`` | ``episodes`` | ``harness_events`` | ``all``.
        workspace:     Filter by project / workspace identity.
        since:         Lower bound for timestamps -- duration ('24h') or
                       date ('2026-05-01'). See :func:`parse_when`.
        until:         Upper bound for timestamps -- same format as ``since``.
        last:          Per-surface row limit (default 20).
        type:          Filter by type column (memory.type, episodes.type,
                       harness_events.type).
        agent:         Filter by agent column (episodes / harness_events).
                       Has no effect on ``memory`` surface.
        command_like:  SQL LIKE pattern matched against
                       ``harness_events.result`` (where command lines are
                       captured for command.executed events). Other surfaces
                       ignore this filter.
        failed:        When True, restrict to failure-y rows
                       (episodes: plan_status IN BLOCKED/NEEDS_INPUT or
                       outcome != success; harness_events: severity=error or
                       result starting with fail/error). Memory surface
                       has no notion of "failed" -- ignored there.
        metrics:       When True, the episodes surface projects the per-turn
                       telemetry stored in ``episodes.context_metrics``
                       (compliance_score, token counts, duration_ms,
                       tool/api call counts, model_used) via json_extract.
                       Only affects the episodes surface. Pair with
                       :func:`aggregate_metrics` for per-agent/-surface
                       rollups.
        db_path:       Optional explicit substrate path (tests).

    Returns:
        Normalized list of dicts, each with keys
        ``surface, timestamp, type, agent, summary, raw``.
    """
    if surface not in VALID_SURFACES:
        raise ValueError(
            f"invalid surface '{surface}'; must be one of {list(VALID_SURFACES)}"
        )

    since_iso = parse_when(since) if since else None
    until_iso = parse_when(until) if until else None

    con = _connect(db_path)
    try:
        results: list[dict] = []
        if surface in ("memory", "all"):
            results.extend(_query_memory(
                con,
                workspace=workspace,
                since_iso=since_iso,
                until_iso=until_iso,
                type_filter=type,
                limit=last,
            ))
        if surface in ("episodes", "all"):
            results.extend(_query_episodes(
                con,
                workspace=workspace,
                since_iso=since_iso,
                until_iso=until_iso,
                type_filter=type,
                agent_filter=agent,
                failed=failed,
                limit=last,
                metrics=metrics,
            ))
        if surface in ("harness_events", "all"):
            results.extend(_query_harness_events(
                con,
                workspace=workspace,
                since_iso=since_iso,
                until_iso=until_iso,
                type_filter=type,
                agent_filter=agent,
                command_like=command_like,
                failed=failed,
                limit=last,
            ))
    finally:
        con.close()

    # Sort merged result newest-first by timestamp string (ISO8601 sorts
    # lexicographically). Empty timestamps sink to the bottom.
    results.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return results


# ---------------------------------------------------------------------------
# Episodic FTS5 search -- canonical reader over episodes_fts in gaia.db
# ---------------------------------------------------------------------------
#
# episodes_fts is a content-linked FTS5 table (content='episodes') kept in
# sync by INSERT/UPDATE/DELETE triggers declared in schema.sql. This is the
# single canonical full-text index over episodic memory; the legacy
# tools/memory/search_store.py (a separate search.db file) was retired in
# favour of these two helpers, which both the ``gaia memory search`` CLI and
# the context injector (tools/context/context_provider.py) call.


def sanitize_episodes_fts_query(query: str) -> str:
    """Turn a free-text string into an FTS5-safe prefix query.

    Each whitespace-delimited word becomes a prefix term (``word*``) so that
    "approval" matches "approvals"/"approving". Hyphens are treated as token
    separators (FTS5 strips them at index time), and characters that would
    break FTS5 MATCH syntax (quotes, stray ``*``) are removed. An empty or
    whitespace-only input yields an empty string, which callers use to skip
    the query entirely.

    This preserves the tokenisation behaviour of the retired
    ``search_store._sanitize_query`` so free-text prompts (e.g. the context
    injector's ``user_task``) still produce meaningful matches instead of
    erroring on raw punctuation.
    """
    query = (query or "").replace("-", " ")
    words = query.split()
    safe = [w.replace('"', "").replace("'", "").strip("*") for w in words if w]
    return " ".join(w + "*" for w in safe if w)


def search_episodes_fts(
    query: str,
    *,
    workspace: str | None = None,
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """FTS5 search over ``episodes_fts`` in gaia.db.

    Returns a list of episode dicts (all columns of the matched ``episodes``
    row) enriched with an ``fts_rank`` field (bm25 rank; lower = more
    relevant). Results are ordered by rank. When ``workspace`` is given the
    match is restricted to that workspace.

    Fails safe: any import/DB/MATCH error yields an empty list so callers
    (CLI search, context injection) never block on a search failure. The
    ``query`` is passed to MATCH verbatim -- callers wanting prefix/free-text
    behaviour should pre-process it with :func:`sanitize_episodes_fts_query`.
    """
    if not query or not query.strip():
        return []
    try:
        con = _connect(db_path)
    except Exception:
        return []
    try:
        if workspace:
            rows = con.execute(
                "SELECT e.*, rank AS fts_rank "
                "FROM episodes_fts "
                "JOIN episodes e ON e.rowid = episodes_fts.rowid "
                "WHERE episodes_fts MATCH ? AND e.workspace = ? "
                "ORDER BY rank "
                "LIMIT ?",
                (query, workspace, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT e.*, rank AS fts_rank "
                "FROM episodes_fts "
                "JOIN episodes e ON e.rowid = episodes_fts.rowid "
                "WHERE episodes_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Curated-memory lineage / history reads (the READ layer for memory history)
# ---------------------------------------------------------------------------
#
# These helpers back `gaia memory show --links|--history` and `gaia memory
# story <slug>`. They are strictly READ-ONLY and batch-oriented: every access
# over a set of memory slugs is a single IN-list query per direction, never one
# query per node (no N+1). The access patterns are index-aligned:
#   * memory_links src side -> memory_links_src(workspace, src_name)
#   * memory_links dst side -> idx_memory_links_dst_kind(workspace, dst_name, kind)
#   * memory_history        -> idx_memory_history_workspace_name(workspace, name)
#   * memory final state    -> PK (workspace, name)
#
# NOTE on approximate birth: the `memory` table has NO created_at column, so a
# node's "birth" can only be APPROXIMATED as the earliest trace we can observe
# (its first memory_history row or its first memory_links edge). Timeline birth
# events are flagged ``approximate=True`` for exactly this reason. We do NOT add
# columns or migrations here -- the approximation is the honest reading of the
# current schema.

_MEMORY_LINEAGE_MAX_DEPTH = 5


def _ro_connect(db_path: Path | None = None) -> sqlite3.Connection:
    """A read-only view onto the substrate.

    Reuses the shared ``_connect`` (which lazily bootstraps the schema on a
    fresh DB), then flips ``PRAGMA query_only = ON`` so every statement issued
    afterwards is guaranteed side-effect free. Bootstrap runs INSIDE ``_connect``
    before the pragma, so a brand-new DB still materializes correctly while the
    caller's queries can only read. This is the read-only mode the repo pattern
    permits without breaking the lazy-bootstrap contract.
    """
    con = _connect(db_path)
    try:
        con.execute("PRAGMA query_only = ON")
    except Exception:
        # query_only is available on every SQLite we ship against; if a build
        # lacks it, fall back to the SELECT-only discipline of this module.
        pass
    return con


def _dedup_preserve(names) -> list[str]:
    """De-duplicate ``names`` preserving first-seen order, dropping falsy."""
    return list(dict.fromkeys(n for n in names if n))


def get_memory_class_status(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Return ``{'class':..., 'status':...}`` for one curated memory row.

    Read-only. This exists to fill the ``gaia memory show --json`` gap: the
    writer's ``get_memory`` projects neither column, so the CLI enriches its
    row with this. Returns ``{'class': None, 'status': None}`` when the row is
    absent (the caller has already resolved existence via ``get_memory``).
    """
    con = _ro_connect(db_path)
    try:
        row = con.execute(
            "SELECT class, status FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            return {"class": None, "status": None}
        return {"class": row["class"], "status": row["status"]}
    finally:
        con.close()


def memory_links_for(
    workspace: str,
    names,
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Batch-load every ``memory_links`` edge that touches any of ``names``.

    Two index-aligned IN-list queries (outgoing on src, incoming on dst),
    merged and de-duplicated by ``(src_name, dst_name, kind)``. Never one query
    per node. Each edge dict: ``{src_name, dst_name, kind, created_at}``.
    """
    names = _dedup_preserve(names)
    if not names:
        return []
    con = _ro_connect(db_path)
    try:
        ph = ",".join("?" * len(names))
        out_rows = con.execute(
            f"SELECT src_name, dst_name, kind, created_at FROM memory_links "
            f"WHERE workspace = ? AND src_name IN ({ph})",
            [workspace, *names],
        ).fetchall()
        in_rows = con.execute(
            f"SELECT src_name, dst_name, kind, created_at FROM memory_links "
            f"WHERE workspace = ? AND dst_name IN ({ph})",
            [workspace, *names],
        ).fetchall()
    finally:
        con.close()
    edges: list[dict] = []
    seen: set = set()
    for r in list(out_rows) + list(in_rows):
        key = (r["src_name"], r["dst_name"], r["kind"])
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "src_name": r["src_name"],
            "dst_name": r["dst_name"],
            "kind": r["kind"],
            "created_at": r["created_at"],
        })
    return edges


def memory_history_for(
    workspace: str,
    names,
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Batch-load ``memory_history`` rows for a set of slugs, oldest first.

    One IN-list query over ``idx_memory_history_workspace_name``. Ordered by
    ``(changed_at, id)`` so ties within the same second keep insertion order.
    Bodies ARE returned (callers compute size deltas, then discard) -- the CLI
    is responsible for not dumping full bodies by default.
    """
    names = _dedup_preserve(names)
    if not names:
        return []
    con = _ro_connect(db_path)
    try:
        ph = ",".join("?" * len(names))
        rows = con.execute(
            f"SELECT id, name, changed_at, "
            f"       before_body, after_body, "
            f"       before_status, after_status, "
            f"       before_type, after_type, "
            f"       before_description, after_description, "
            f"       before_workspace, after_workspace, "
            f"       before_deleted_at, after_deleted_at "
            f"FROM memory_history "
            f"WHERE workspace = ? AND name IN ({ph}) "
            f"ORDER BY changed_at, id",
            [workspace, *names],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _role_for_edge(kind: str, direction: str) -> str:
    """Map an edge (kind + discovery direction) to the neighbour's tree role.

    ``direction`` is relative to the frontier node that discovered the
    neighbour: ``out`` means frontier=src / neighbour=dst; ``in`` means
    frontier=dst / neighbour=src.
    """
    table = {
        ("supersedes", "out"): "superseded",       # neighbour is the older, replaced row
        ("supersedes", "in"): "successor",         # neighbour replaces the frontier row
        ("graduated_to", "out"): "graduation_target",
        ("graduated_to", "in"): "graduated_thread",
        ("derived_from", "out"): "source",         # neighbour is what we derived from
        ("derived_from", "in"): "derivative",
        ("relates_to", "out"): "related",
        ("relates_to", "in"): "related",
    }
    return table.get((kind, direction), "related")


def memory_lineage(
    workspace: str,
    slug: str,
    *,
    max_depth: int = _MEMORY_LINEAGE_MAX_DEPTH,
    db_path: Path | None = None,
) -> dict:
    """BFS the ``memory_links`` graph from ``slug`` in BOTH directions.

    Uses a visited-set (cycle-safe) and stops after ``max_depth`` hops. At each
    depth level it issues exactly TWO batch queries (outgoing over the frontier
    IN-list, incoming over the frontier IN-list) -- never one per node -- so a
    deep lineage costs ``2 * hops`` queries, not ``2 * nodes``.

    Returns::

        {
            "seed":   slug,
            "nodes":  [name, ...],            # sorted, includes seed
            "depth":  {name: hop_count},
            "role":   {name: role_label},     # seed -> "queried"
            "edges":  [{src_name, dst_name, kind, created_at}, ...],
        }
    """
    con = _ro_connect(db_path)
    try:
        visited = {slug}
        depth = {slug: 0}
        role = {slug: "queried"}
        edges: list[dict] = []
        edge_seen: set = set()
        frontier = [slug]
        hop = 0
        while frontier and hop < max_depth:
            ph = ",".join("?" * len(frontier))
            out_rows = con.execute(
                f"SELECT src_name, dst_name, kind, created_at FROM memory_links "
                f"WHERE workspace = ? AND src_name IN ({ph})",
                [workspace, *frontier],
            ).fetchall()
            in_rows = con.execute(
                f"SELECT src_name, dst_name, kind, created_at FROM memory_links "
                f"WHERE workspace = ? AND dst_name IN ({ph})",
                [workspace, *frontier],
            ).fetchall()
            next_frontier: list[str] = []
            for r, direction in (
                [(x, "out") for x in out_rows] + [(x, "in") for x in in_rows]
            ):
                key = (r["src_name"], r["dst_name"], r["kind"])
                if key not in edge_seen:
                    edge_seen.add(key)
                    edges.append({
                        "src_name": r["src_name"],
                        "dst_name": r["dst_name"],
                        "kind": r["kind"],
                        "created_at": r["created_at"],
                    })
                neighbor = r["dst_name"] if direction == "out" else r["src_name"]
                if neighbor not in visited:
                    visited.add(neighbor)
                    depth[neighbor] = hop + 1
                    role[neighbor] = _role_for_edge(r["kind"], direction)
                    next_frontier.append(neighbor)
            frontier = next_frontier
            hop += 1
    finally:
        con.close()
    return {
        "seed": slug,
        "nodes": sorted(visited),
        "depth": depth,
        "role": role,
        "edges": edges,
    }


def memory_final_states(
    workspace: str,
    names,
    roles: dict | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict]:
    """Batch-load the CURRENT state of each node (one IN-list query over PK).

    Each dict: ``{name, class, status, type, description, updated_at,
    deleted_at, present, role}``. ``present`` is False for a node that appears
    in the lineage graph but whose ``memory`` row no longer exists (e.g. a
    hard-deleted endpoint of an edge). ``role`` comes from ``roles`` (the
    lineage role map) when supplied.
    """
    names = _dedup_preserve(names)
    roles = roles or {}
    if not names:
        return []
    con = _ro_connect(db_path)
    try:
        ph = ",".join("?" * len(names))
        rows = con.execute(
            f"SELECT name, class, status, type, description, updated_at, "
            f"       deleted_at "
            f"FROM memory WHERE workspace = ? AND name IN ({ph})",
            [workspace, *names],
        ).fetchall()
    finally:
        con.close()
    found = {r["name"]: dict(r) for r in rows}
    out: list[dict] = []
    for n in names:
        r = found.get(n)
        out.append({
            "name": n,
            "class": r["class"] if r else None,
            "status": r["status"] if r else None,
            "type": r["type"] if r else None,
            "description": r["description"] if r else None,
            "updated_at": r["updated_at"] if r else None,
            "deleted_at": r["deleted_at"] if r else None,
            "present": r is not None,
            "role": roles.get(n),
        })
    return out


def _fuse_timeline(node_set: set, history: list[dict], edges: list[dict]) -> list[dict]:
    """Fuse per-node history + link creation into ONE chronological timeline.

    Event kinds: ``birth`` (approximate first trace), ``append`` (body grew as
    a prefix-preserving concatenation, with ``body_delta``), ``edit`` (body
    changed otherwise), ``status`` (lifecycle transition), ``description``,
    ``retype``, ``relocate``, ``tombstone``/``restore``, and ``link`` (edge
    created). Sorted by ``(changed_at, bucket, seq)`` so births lead, then
    history events in insertion order, then links -- deterministic even when
    several rows share the same second-resolution ``changed_at``.
    """
    keyed: list[tuple] = []  # (sortkey, event)

    # Approximate birth: earliest trace per node across history + edges.
    earliest: dict = {}
    for h in history:
        n, ts = h["name"], h["changed_at"]
        if ts and (n not in earliest or ts < earliest[n]):
            earliest[n] = ts
    for e in edges:
        ts = e["created_at"]
        for n in (e["src_name"], e["dst_name"]):
            if n in node_set and ts and (n not in earliest or ts < earliest[n]):
                earliest[n] = ts
    for n, ts in earliest.items():
        ev = {
            "ts": ts, "node": n, "kind": "birth", "approximate": True,
            "detail": "first trace (approximate birth -- memory has no created_at)",
        }
        keyed.append(((ts, 0, 0), ev))

    # History-derived events. bucket=1, seq=history row id for stable ordering.
    for h in history:
        n, ts, seq = h["name"], h["changed_at"], h["id"]
        bb, ab = (h["before_body"] or ""), (h["after_body"] or "")
        if bb != ab:
            delta = len(ab) - len(bb)
            if ab.startswith(bb) and delta > 0:
                ev = {"ts": ts, "node": n, "kind": "append",
                      "body_delta": delta, "detail": f"append (+{delta} chars)"}
            else:
                sign = "+" if delta >= 0 else ""
                ev = {"ts": ts, "node": n, "kind": "edit",
                      "body_delta": delta,
                      "detail": f"edit body ({sign}{delta} chars)"}
            keyed.append(((ts, 1, seq), ev))
        if (h["before_status"] or None) != (h["after_status"] or None):
            frm = h["before_status"] or "(none)"
            to = h["after_status"] or "(none)"
            keyed.append(((ts, 1, seq), {
                "ts": ts, "node": n, "kind": "status",
                "from": h["before_status"], "to": h["after_status"],
                "detail": f"status {frm} -> {to}",
            }))
        if (h["before_description"] or None) != (h["after_description"] or None):
            keyed.append(((ts, 1, seq), {
                "ts": ts, "node": n, "kind": "description",
                "detail": "description changed",
            }))
        if (h["before_type"] or None) != (h["after_type"] or None):
            keyed.append(((ts, 1, seq), {
                "ts": ts, "node": n, "kind": "retype",
                "detail": f"type {h['before_type']} -> {h['after_type']}",
            }))
        if (h["before_workspace"] or None) != (h["after_workspace"] or None):
            keyed.append(((ts, 1, seq), {
                "ts": ts, "node": n, "kind": "relocate",
                "detail": (
                    f"workspace {h['before_workspace']} -> {h['after_workspace']}"
                ),
            }))
        if (h["before_deleted_at"] or None) != (h["after_deleted_at"] or None):
            if h["after_deleted_at"]:
                keyed.append(((ts, 1, seq), {
                    "ts": ts, "node": n, "kind": "tombstone",
                    "detail": "tombstoned (soft-delete)",
                }))
            else:
                keyed.append(((ts, 1, seq), {
                    "ts": ts, "node": n, "kind": "restore",
                    "detail": "restored from tombstone",
                }))

    # Link creation events. bucket=2 so a link created in the same second as a
    # body change reads after it.
    for e in edges:
        ts = e["created_at"]
        keyed.append(((ts, 2, 0), {
            "ts": ts, "node": e["src_name"], "kind": "link",
            "detail": f"{e['src_name']} -[{e['kind']}]-> {e['dst_name']}",
        }))

    # Sort chronologically. A missing ts sinks to the bottom deterministically.
    keyed.sort(key=lambda pair: (
        pair[0][0] or "9999-99-99", pair[0][1], pair[0][2] or 0,
    ))
    return [ev for _k, ev in keyed]


def build_memory_story(
    workspace: str,
    slug: str,
    *,
    max_depth: int = _MEMORY_LINEAGE_MAX_DEPTH,
    db_path: Path | None = None,
) -> dict:
    """Assemble the full lineage story for ``slug`` as a plain-data dict.

    Resolves the lineage (BFS), batch-loads history and final states for the
    whole node set, and fuses everything into one chronological timeline.
    Pure read-only, batch (no N+1). Returns::

        {
            "seed": slug,
            "nodes": [{name, depth, role}, ...],
            "edges": [{src_name, dst_name, kind, created_at}, ...],
            "timeline": [event, ...],
            "final_states": [{name, class, status, type, ...}, ...],
        }
    """
    lin = memory_lineage(workspace, slug, max_depth=max_depth, db_path=db_path)
    names = lin["nodes"]
    node_set = set(names)
    history = memory_history_for(workspace, names, db_path=db_path)
    finals = memory_final_states(workspace, names, lin["role"], db_path=db_path)
    timeline = _fuse_timeline(node_set, history, lin["edges"])
    nodes = [
        {"name": n, "depth": lin["depth"].get(n), "role": lin["role"].get(n)}
        for n in names
    ]
    return {
        "seed": slug,
        "nodes": nodes,
        "edges": lin["edges"],
        "timeline": timeline,
        "final_states": finals,
    }


__all__ = [
    "VALID_SURFACES",
    "VALID_GROUP_BY",
    "parse_when",
    "cross_surface_query",
    "group_and_count",
    "aggregate_metrics",
    "sanitize_episodes_fts_query",
    "search_episodes_fts",
    "_highlight_snippet",
    "_extract_text_needle",
    "_row_text_for_snippet",
    # Curated-memory lineage / history read layer
    "get_memory_class_status",
    "memory_links_for",
    "memory_history_for",
    "memory_lineage",
    "memory_final_states",
    "build_memory_story",
]
