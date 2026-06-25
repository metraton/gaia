#!/usr/bin/env python3
"""
Risk 4 regression (Brief 54 / Task 2.2): the SessionStart "Recent Events"
block reads from harness_events via cross_surface_query, whose row shape is
{surface, timestamp, type, agent, summary, raw} -- NOT the legacy JSONL shape
{ts, type, agent, result}.

The context_injector formatting loop was remapped to the reader's keys. If the
remap regresses (e.g. someone restores evt.get("ts")/evt.get("result")), every
rendered line would silently go blank in the timestamp/result fields. This test
seeds the DB, runs the SAME query + formatting the injector uses, and asserts
the rendered block carries the real values.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from gaia.store import reader as store_reader
from gaia.store import writer as store_writer


def _render_recent_events(rows):
    """Mirror of the context_injector formatting loop (remapped keys).

    Kept in lockstep with hooks/modules/context/context_injector.py. If the
    injector's key mapping changes, this helper must change with it -- the
    assertions below then prove the rendered output is non-blank.
    """
    lines = ["\n# Recent Events (last 24h)"]
    for evt in rows:
        ts_short = (evt.get("timestamp") or "")[:16]
        etype = evt.get("type") or ""
        agent_name = evt.get("agent") or ""
        result_str = evt.get("summary") or ""
        label = f"{agent_name}: " if agent_name else ""
        lines.append(f"- [{ts_short}] {etype}: {label}{result_str}")
    return "\n".join(lines)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "gaia.db"


def test_recent_events_block_not_blank(db_path):
    """Seeded harness_events render into a non-blank Recent Events block."""
    store_writer.write_harness_event(
        event_type="agent.dispatch",
        source="hook",
        agent="developer",
        result="dispatched for: build the feature",
        workspace="me",
        db_path=db_path,
    )
    store_writer.write_harness_event(
        event_type="command.executed",
        source="hook",
        agent="",
        result="ok: git status",
        workspace="me",
        db_path=db_path,
    )

    rows = store_reader.cross_surface_query(
        surface="harness_events", since="24h", last=20, db_path=db_path,
    )
    assert rows, "reader returned no harness_events rows"

    block = _render_recent_events(rows)

    # The block must not be just the header (i.e. the loop produced lines).
    body_lines = [l for l in block.splitlines() if l.startswith("- [")]
    assert len(body_lines) == 2

    # Risk 4: timestamp, type, agent, and summary must all be present --
    # a regressed remap (evt.get("ts")/("result")) would blank these.
    assert "agent.dispatch" in block
    assert "command.executed" in block
    assert "developer:" in block
    assert "dispatched for: build the feature" in block
    assert "ok: git status" in block
    # No line may have an empty [timestamp] or trailing-empty type.
    for line in body_lines:
        assert "[]" not in line, f"blank timestamp in: {line!r}"
        ts_part = line.split("]")[0]
        assert len(ts_part) > 2, f"blank timestamp in: {line!r}"


def test_remap_keys_match_reader_output_shape(db_path):
    """The keys the injector reads are exactly the reader's output keys."""
    store_writer.write_harness_event(
        event_type="session.end", source="hook", result="ended",
        workspace="me", db_path=db_path,
    )
    rows = store_reader.cross_surface_query(
        surface="harness_events", since="24h", last=20, db_path=db_path,
    )
    assert rows
    row = rows[0]
    # The four keys the injector formatting loop reads.
    for key in ("timestamp", "type", "agent", "summary"):
        assert key in row, f"reader output missing key {key!r}"
    # The legacy keys must NOT be what we rely on (guard against accidental
    # reintroduction of the JSONL shape in the reader).
    assert "ts" not in row
    assert "result" not in row
