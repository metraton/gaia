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

    sql = (
        "SELECT episode_id, workspace, timestamp, session_id, task_id, agent, "
        "type, title, plan_status, outcome, exit_code, duration_seconds "
        "FROM episodes"
    )
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


__all__ = [
    "VALID_SURFACES",
    "VALID_GROUP_BY",
    "parse_when",
    "cross_surface_query",
    "group_and_count",
    "_highlight_snippet",
    "_extract_text_needle",
    "_row_text_for_snippet",
]
