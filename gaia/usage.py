"""Token usage from Claude Code transcripts: ingestion into ``token_usage`` and
per-plan / per-session reports over it.

A transcript line of type ``assistant`` carries ``message.id`` and
``message.usage``. One API message spans several lines (one per content block,
each repeating the usage, the last one carrying the final ``output_tokens``),
so rows are keyed by ``(session_id, message_id)`` and each counter keeps its
maximum. That makes ingestion idempotent: re-reading a transcript, or a
resumed subagent's transcript that grew, never adds a message twice.

Layout read under a projects directory (``~/.claude/projects`` by default)::

    <project>/<session_id>.jsonl                      main thread
    <project>/<session_id>/subagents/agent-<id>.jsonl  one subagent
    <project>/<session_id>/subagents/agent-<id>.meta.json  {"agentType": ...}
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

MAIN_AGENT_TYPE = "main"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_COUNTERS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
_USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
}


@dataclass(frozen=True)
class Transcript:
    path: Path
    session_id: str
    harness_agent_id: Optional[str]
    agent_type: str


def discover_transcripts(projects_dir: Path, session_id: Optional[str] = None) -> Iterator[Transcript]:
    """Yield every main and subagent transcript under ``projects_dir``,
    restricted to one session when ``session_id`` is given."""
    pattern = f"{session_id}.jsonl" if session_id else "*.jsonl"
    for main in sorted(projects_dir.glob(f"*/{pattern}")):
        sid = main.stem
        yield Transcript(main, sid, None, MAIN_AGENT_TYPE)
        for sub in sorted((main.parent / sid / "subagents").glob("agent-*.jsonl")):
            yield subagent_transcript(sub, sid)


def subagent_transcript(path: Path, session_id: str) -> Transcript:
    """Describe one ``agent-<id>.jsonl``; its agent type comes from the sibling
    ``.meta.json`` and falls back to ``unknown`` when that file is missing."""
    agent_id = path.stem[len("agent-"):]
    agent_type = "unknown"
    meta = path.with_suffix(".meta.json")
    try:
        agent_type = json.loads(meta.read_text()).get("agentType") or agent_type
    except (OSError, ValueError):
        pass
    return Transcript(path, session_id, agent_id, agent_type)


def read_messages(transcript: Transcript) -> dict:
    """Collapse a transcript's assistant lines into one row per message id."""
    rows: dict = {}
    try:
        handle = transcript.path.open(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    with handle:
        for line in handle:
            if '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            if entry.get("type") != "assistant" or not isinstance(message, dict):
                continue
            usage, message_id = message.get("usage"), message.get("id")
            if not isinstance(usage, dict) or not message_id:
                continue
            counts = {col: int(usage.get(key) or 0) for col, key in _USAGE_KEYS.items()}
            row = rows.get(message_id)
            if row is None:
                rows[message_id] = {
                    "session_id": entry.get("sessionId") or transcript.session_id,
                    "message_id": message_id,
                    "harness_agent_id": transcript.harness_agent_id,
                    "agent_type": transcript.agent_type,
                    "model": message.get("model"),
                    "timestamp": entry.get("timestamp") or "",
                    "transcript_path": str(transcript.path),
                    **counts,
                }
            else:
                for col in _COUNTERS:
                    row[col] = max(row[col], counts[col])
    return rows


_UPSERT = """
INSERT INTO token_usage (session_id, message_id, harness_agent_id, agent_type, model,
                         timestamp, input_tokens, output_tokens, cache_creation_tokens,
                         cache_read_tokens, transcript_path)
VALUES (:session_id, :message_id, :harness_agent_id, :agent_type, :model, :timestamp,
        :input_tokens, :output_tokens, :cache_creation_tokens, :cache_read_tokens,
        :transcript_path)
ON CONFLICT (session_id, message_id) DO UPDATE SET
    input_tokens          = MAX(input_tokens, excluded.input_tokens),
    output_tokens         = MAX(output_tokens, excluded.output_tokens),
    cache_creation_tokens = MAX(cache_creation_tokens, excluded.cache_creation_tokens),
    cache_read_tokens     = MAX(cache_read_tokens, excluded.cache_read_tokens)
"""


def ingest(con: sqlite3.Connection, transcripts: Iterable[Transcript]) -> dict:
    """Upsert every message of ``transcripts``; returns files read, messages
    seen, and how many rows the table gained (0 on a repeated run)."""
    before = con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
    files = messages = 0
    for transcript in transcripts:
        rows = read_messages(transcript)
        files += 1
        messages += len(rows)
        con.executemany(_UPSERT, list(rows.values()))
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0]
    return {"files": files, "messages": messages, "new_rows": after - before}


# A handoff row is bound to the plan when it executes one of the plan's tasks,
# names the plan, or is a verifier whose parent row is bound that way.
_BOUND_AGENTS_SQL = """
WITH producer AS (
    SELECT h.id, h.harness_agent_id FROM agent_contract_handoffs h
    WHERE h.plan_id = :plan_id
       OR h.plan_task_id IN (SELECT id FROM tasks WHERE plan_id = :plan_id)
)
SELECT harness_agent_id FROM producer WHERE harness_agent_id IS NOT NULL
UNION
SELECT h.harness_agent_id FROM agent_contract_handoffs h
WHERE h.parent_handoff_id IN (SELECT id FROM producer) AND h.harness_agent_id IS NOT NULL
"""


def plan_window(con: sqlite3.Connection, plan_id: int) -> Optional[dict]:
    """Plan identity and its default window: created_at until closure (the
    plan's updated_at once closed, open-ended otherwise)."""
    row = con.execute(
        "SELECT p.id, p.status, p.created_at, p.updated_at, b.name, b.workspace "
        "FROM plans p JOIN briefs b ON b.id = p.brief_id WHERE p.id = ?",
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "plan_id": row[0], "status": row[1], "brief": row[4], "workspace": row[5],
        "since": row[2], "until": row[3] if row[1] == "closed" else None,
    }


def plan_report(con: sqlite3.Connection, plan_id: int, since: Optional[str] = None,
                until: Optional[str] = None) -> Optional[dict]:
    """Tokens of the sessions that worked on ``plan_id`` inside its window,
    split per session and per agent type into bound (subagents whose contract
    row is bound to the plan), unbound subagents, and the main thread."""
    plan = plan_window(con, plan_id)
    if plan is None:
        return None
    since = since or plan["since"]
    until = until or plan["until"] or "9999"
    bound = {r[0] for r in con.execute(_BOUND_AGENTS_SQL, {"plan_id": plan_id})}
    sessions = _sessions_of(con, bound)
    rows = _usage_rows(con, sessions, _iso_floor(since), _iso_floor(until))
    groups = [dict(r, binding=_binding(r, bound)) for r in rows]
    return {
        "plan": dict(plan, since=since, until=None if until == "9999" else until),
        "bound_agents_known": len(bound),
        "bound_agents_with_usage": len({g["harness_agent_id"] for g in groups
                                        if g["binding"] == "bound"}),
        "by_session": _rollup(groups, ("session_id", "binding")),
        "by_agent": _rollup(groups, ("agent_type", "binding")),
        "total": _rollup(groups, ())[0] if groups else None,
    }


def session_report(con: sqlite3.Connection, session_id: str) -> dict:
    """Tokens of one session per agent type, main thread included."""
    rows = [dict(r, binding="-") for r in _usage_rows(con, [session_id], "", "9999")]
    return {
        "session_id": session_id,
        "by_agent": _rollup(rows, ("agent_type",)),
        "total": _rollup(rows, ())[0] if rows else None,
    }


def _iso_floor(value: str) -> str:
    # Transcripts stamp '...T15:30:57.270Z' and Gaia rows '...T15:30:53Z';
    # dropping the zone suffix keeps the two comparable as strings.
    return value[:-1] if value.endswith("Z") else value


def _sessions_of(con: sqlite3.Connection, agent_ids: set) -> list:
    if not agent_ids:
        return []
    marks = ",".join("?" * len(agent_ids))
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT session_id FROM token_usage WHERE harness_agent_id IN ({marks})",
        tuple(agent_ids))]


def _usage_rows(con: sqlite3.Connection, sessions: list, since: str, until: str) -> list:
    if not sessions:
        return []
    marks = ",".join("?" * len(sessions))
    cur = con.execute(
        "SELECT session_id, harness_agent_id, agent_type, COUNT(*) AS calls, "
        "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
        "SUM(cache_creation_tokens) AS cache_creation_tokens, "
        "SUM(cache_read_tokens) AS cache_read_tokens, "
        "MIN(timestamp) AS first_at, MAX(timestamp) AS last_at "
        f"FROM token_usage WHERE session_id IN ({marks}) "
        "AND timestamp >= ? AND timestamp <= ? "
        "GROUP BY session_id, harness_agent_id, agent_type",
        (*sessions, since, until),
    )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _binding(row: dict, bound: set) -> str:
    if row["harness_agent_id"] is None:
        return "main"
    return "bound" if row["harness_agent_id"] in bound else "unbound"


def _rollup(rows: list, keys: tuple) -> list:
    out: dict = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        acc = out.setdefault(key, {**{k: row[k] for k in keys}, "agents": set(), "calls": 0,
                                   **{c: 0 for c in _COUNTERS}})
        if row["harness_agent_id"]:
            acc["agents"].add(row["harness_agent_id"])
        acc["calls"] += row["calls"]
        for col in _COUNTERS:
            acc[col] += row[col] or 0
    result = []
    for acc in out.values():
        acc["subagents"] = len(acc.pop("agents"))
        result.append(acc)
    return sorted(result, key=lambda r: -r["output_tokens"])
