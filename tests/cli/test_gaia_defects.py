#!/usr/bin/env python3
"""
Reader-level coverage for `gaia defects` (gaia.store.reader.read_defects).

Every test runs against a temporary substrate seeded by `seeded_db`, never the
developer's own gaia.db.

The seed is built so each filter has rows it MUST return and rows it MUST
exclude -- a filter that returned everything would pass a "did it return
something" check and fail here. Three properties of the reader get pinned
because they are decisions rather than mechanics:

- Agent resolution is two-step. `episode_anomalies` has no agent column: the
  reader COALESCEs `episodes.agent` (via LEFT JOIN) with `payload.agent`, and
  the payload read is wrapped in `json_valid` because the column is free-form.
  A row whose payload is not JSON, is NULL, or simply has no agent key must
  resolve to None rather than failing the query for every other row.
- The orchestrator channel is included BY SEVERITY, not by an enumerated list
  of event types: any `harness_events` row graded above `info` is a defect.
  `contract.rejected` in the seed is a type the reader has never heard of and
  must still return.
- The two channels are merged into one newest-first listing, so the expected
  order below interleaves them.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gaia.store.reader import NON_DEFECT_EVENT_SEVERITIES, read_defects  # noqa: E402

WS = "triage-ws"
OTHER_WS = "other-ws"

EP_JOINED = "ep_joined"
EP_AGENTLESS = "ep_agentless"
EP_OTHER = "ep_other"

# Anomalies: (id_label, episode, ts, type, severity, payload)
ANOMALY_SEED = [
    ("A1", EP_JOINED, "2026-07-20T09:00:00+00:00", "skipped_verification",
     "warning", json.dumps({"agent": "payload-should-lose"})),
    ("A2", EP_AGENTLESS, "2026-07-21T09:00:00+00:00", "agent_reported_defect",
     "critical", json.dumps({"agent": "gaia-operator"})),
    ("A3", EP_AGENTLESS, "2026-07-19T09:00:00+00:00", "agent_reported_defect",
     "info", "{not json at all"),
    ("A4", EP_AGENTLESS, "2026-07-18T09:00:00+00:00", "empty_evidence",
     "warning", None),
    ("A5", EP_AGENTLESS, "2026-07-17T09:00:00+00:00", "duplicate_tools",
     "info", json.dumps({"note": "no agent key"})),
]

# Harness events: (id_label, workspace, ts, type, severity, agent)
EVENT_SEED = [
    ("H1", WS, "2026-07-22T09:00:00+00:00", "agent.cut", "warning",
     "platform-architect"),
    ("H2", WS, "2026-07-22T10:00:00+00:00", "agent.complete", "info",
     "developer"),
    ("H3", WS, "2026-07-23T09:00:00+00:00", "contract.rejected", "error",
     "gaia-verifier"),
    ("H4", WS, "2026-07-23T10:00:00+00:00", "session.end", None, None),
    ("H5", None, "2026-07-16T09:00:00+00:00", "agent.cut", "warning",
     "gaia-system"),
    ("H6", WS, "2026-07-24T09:00:00+00:00", "debug.trace", "debug",
     "developer"),
]

# Rows belonging to the second workspace, present so workspace scoping has
# something to exclude.
O1_TS = "2026-07-21T12:00:00+00:00"  # anomaly
O2_TS = "2026-07-24T10:00:00+00:00"  # harness event

# The rows the workspace-scoped reader must return, newest first. Labels are
# resolved back to rows by timestamp, which is unique across the seed.
EXPECTED_ORDER = ["H3", "H1", "A2", "A1", "A3", "A4", "A5", "H5"]

_TS_BY_LABEL = {anomaly[0]: anomaly[2] for anomaly in ANOMALY_SEED}
_TS_BY_LABEL.update({event[0]: event[2] for event in EVENT_SEED})
_TS_BY_LABEL.update({"O1": O1_TS, "O2": O2_TS})


def _labels(rows) -> list[str]:
    """Map returned rows back to seed labels via their unique timestamp."""
    by_ts = {ts: label for label, ts in _TS_BY_LABEL.items()}
    return [by_ts[row["timestamp"]] for row in rows]


@pytest.fixture()
def seeded_db(tmp_path) -> Path:
    """An isolated substrate carrying defects of both origins, plus non-defects."""
    from gaia.store.writer import _connect

    db_path = tmp_path / "gaia.db"
    con = _connect(db_path)
    try:
        for name in (WS, OTHER_WS):
            con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (name,))
        for episode_id, workspace, agent in (
            (EP_JOINED, WS, "gaia-system"),
            (EP_AGENTLESS, WS, None),
            (EP_OTHER, OTHER_WS, "developer"),
        ):
            con.execute(
                "INSERT INTO episodes (episode_id, workspace, timestamp, agent) "
                "VALUES (?, ?, ?, ?)",
                (episode_id, workspace, "2026-07-15T09:00:00+00:00", agent),
            )
        for _label, episode_id, ts, type_, severity, payload in ANOMALY_SEED:
            con.execute(
                "INSERT INTO episode_anomalies "
                "(episode_id, workspace, timestamp, type, severity, message, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (episode_id, WS, ts, type_, severity, f"seeded {type_}", payload),
            )
        con.execute(
            "INSERT INTO episode_anomalies "
            "(episode_id, workspace, timestamp, type, severity, message, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (EP_OTHER, OTHER_WS, O1_TS,
             "skipped_verification", "warning", "other workspace", None),
        )
        for _label, workspace, ts, type_, severity, agent in EVENT_SEED:
            con.execute(
                "INSERT INTO harness_events "
                "(workspace, ts, type, source, agent, result, severity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workspace, ts, type_, "hook", agent, f"seeded {type_}", severity),
            )
        con.execute(
            "INSERT INTO harness_events "
            "(workspace, ts, type, source, agent, result, severity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (OTHER_WS, O2_TS, "agent.cut", "hook",
             "developer", "other workspace", "warning"),
        )
        con.commit()
    finally:
        con.close()
    return db_path


def _read(db_path, **kwargs):
    kwargs.setdefault("workspace", WS)
    kwargs.setdefault("limit", 100)
    return read_defects(db_path=db_path, **kwargs)


# ---------------------------------------------------------------------------
# Baseline: what the unfiltered, workspace-scoped read returns
# ---------------------------------------------------------------------------

def test_unfiltered_read_returns_both_origins_newest_first(seeded_db):
    rows = _read(seeded_db)
    assert _labels(rows) == EXPECTED_ORDER
    assert {row["origin"] for row in rows} == {"subagent", "orchestrator"}


def test_other_workspace_rows_are_excluded(seeded_db):
    """Scoping cuts both ways, except for the workspace-less global event.

    H5 carries `workspace IS NULL`, so it belongs to every workspace-scoped
    listing rather than to one -- that is why the second workspace returns it
    alongside its own two rows.
    """
    scoped = set(_labels(_read(seeded_db)))
    assert {"O1", "O2"}.isdisjoint(scoped)

    other = _read(seeded_db, workspace=OTHER_WS)
    assert set(_labels(other)) == {"O1", "O2", "H5"}


def test_no_workspace_filter_spans_every_workspace(seeded_db):
    rows = _read(seeded_db, workspace=None)
    assert len(rows) == len(EXPECTED_ORDER) + 2


# ---------------------------------------------------------------------------
# Agent resolution: LEFT JOIN first, payload second, neither is fatal
# ---------------------------------------------------------------------------

def test_agent_comes_from_the_episode_when_the_episode_has_one(seeded_db):
    rows = _read(seeded_db, type="skipped_verification")
    assert _labels(rows) == ["A1"]
    assert rows[0]["agent"] == "gaia-system", (
        "episodes.agent must win over payload.agent"
    )


def test_agent_falls_back_to_the_payload_when_the_episode_has_none(seeded_db):
    rows = _read(seeded_db, severity="critical")
    assert _labels(rows) == ["A2"]
    assert rows[0]["agent"] == "gaia-operator"


@pytest.mark.parametrize("label", ["A3", "A4", "A5"])
def test_unusable_payload_resolves_to_no_agent_without_failing(seeded_db, label):
    """Malformed, absent, and agent-less payloads must degrade, not raise.

    A3 carries invalid JSON, A4 carries NULL, A5 carries JSON with no agent
    key. Without the json_valid guard, A3 alone would fail the whole query.
    """
    rows = _read(seeded_db)
    row = next(r for r in rows if _labels([r]) == [label])
    assert row["agent"] is None
    assert row["origin"] == "subagent"


def test_agent_filter_matches_across_both_resolution_paths(seeded_db):
    joined = _read(seeded_db, agent="gaia-system")
    assert set(_labels(joined)) == {"A1", "H5"}

    from_payload = _read(seeded_db, agent="gaia-operator")
    assert _labels(from_payload) == ["A2"]

    assert _read(seeded_db, agent="payload-should-lose") == []


# ---------------------------------------------------------------------------
# The orchestrator channel's inclusion rule: severity, not a type list
# ---------------------------------------------------------------------------

def test_orchestrator_channel_includes_by_severity_not_by_event_type(seeded_db):
    rows = _read(seeded_db, origin="orchestrator")
    labels = set(_labels(rows))
    assert labels == {"H1", "H3", "H5"}
    assert "contract.rejected" in {row["type"] for row in rows}, (
        "an event type the reader does not know about must still be returned"
    )


@pytest.mark.parametrize("label", ["H2", "H4", "H6"])
def test_events_at_or_below_info_are_never_defects(seeded_db, label):
    """info, NULL and debug severities stay out, under every filter shape."""
    everywhere = (
        _read(seeded_db)
        + _read(seeded_db, origin="orchestrator")
        + _read(seeded_db, workspace=None)
        + _read(seeded_db, agent="developer")
    )
    assert label not in set(_labels(everywhere))


def test_the_excluded_severities_are_exactly_info_and_debug(seeded_db):
    """Pins the declared rule so a change to it cannot pass unnoticed."""
    assert set(NON_DEFECT_EVENT_SEVERITIES) == {"info", "debug"}


def test_workspaceless_events_are_visible_to_a_workspace_scoped_read(seeded_db):
    """H5 carries workspace NULL -- a global event still belongs to the listing."""
    rows = _read(seeded_db, origin="orchestrator")
    h5 = next(r for r in rows if _labels([r]) == ["H5"])
    assert h5["workspace"] is None


# ---------------------------------------------------------------------------
# The remaining filters, each with rows it must exclude
# ---------------------------------------------------------------------------

def test_type_filter_narrows_to_one_class(seeded_db):
    rows = _read(seeded_db, type="agent_reported_defect")
    assert _labels(rows) == ["A2", "A3"]
    assert len(rows) < len(EXPECTED_ORDER)


def test_severity_filter_is_exact_and_case_insensitive(seeded_db):
    assert set(_labels(_read(seeded_db, severity="WARNING"))) == {
        "A1", "A4", "H1", "H5",
    }
    assert _labels(_read(seeded_db, severity="critical")) == ["A2"]
    assert set(_labels(_read(seeded_db, severity="info"))) == {"A3", "A5"}
    assert _read(seeded_db, severity="fatal") == []


def test_origin_filter_isolates_each_channel(seeded_db):
    subagent = _read(seeded_db, origin="subagent")
    assert {row["origin"] for row in subagent} == {"subagent"}
    assert set(_labels(subagent)) == {"A1", "A2", "A3", "A4", "A5"}

    orchestrator = _read(seeded_db, origin="orchestrator")
    assert {row["origin"] for row in orchestrator} == {"orchestrator"}
    assert set(_labels(orchestrator)) == {"H1", "H3", "H5"}


def test_time_window_bounds_both_channels(seeded_db):
    window = _read(seeded_db, since="2026-07-21", until="2026-07-22T23:59:59")
    assert _labels(window) == ["H1", "A2"]

    assert set(_labels(_read(seeded_db, since="2026-07-23"))) == {"H3"}
    assert set(_labels(_read(seeded_db, until="2026-07-17"))) == {"H5"}


def test_limit_caps_each_channel_independently(seeded_db):
    rows = _read(seeded_db, limit=1)
    assert _labels(rows) == ["H3", "A2"], (
        "the cap is per channel, so one row of each origin survives"
    )


def test_invalid_origin_is_rejected(seeded_db):
    with pytest.raises(ValueError, match="invalid origin"):
        _read(seeded_db, origin="harness")


def test_every_row_carries_the_triage_columns(seeded_db):
    for row in _read(seeded_db):
        assert set(row) == {
            "origin", "id", "timestamp", "type", "severity",
            "agent", "workspace", "message", "source",
        }
        assert row["type"] and row["severity"] and row["timestamp"]
        assert isinstance(row["id"], int)


def test_reader_never_writes_to_the_substrate(seeded_db):
    """A read surface must leave the row counts it read untouched."""
    def counts():
        con = sqlite3.connect(str(seeded_db))
        try:
            return (
                con.execute("SELECT COUNT(*) FROM episode_anomalies").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM harness_events").fetchone()[0],
            )
        finally:
            con.close()

    before = counts()
    _read(seeded_db)
    _read(seeded_db, origin="orchestrator", severity="warning")
    assert counts() == before
