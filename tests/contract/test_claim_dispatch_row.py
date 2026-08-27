"""v43 -- enriched birth + atomic dispatch claim.

Two halves of the dispatch->start DB bridge:

  (1) ENRICHED BIRTH: insert_dispatched_handoff persists the dispatch
      coordinates (dispatch_prompt_id / dispatch_tool_use_id /
      dispatch_description / dispatch_prompt) and the kernel payload
      (context_anchors / kernel_sections, JSON-serialized), all nullable.
  (2) CLAIM: claim_dispatch_row correlates a SubagentStart to its born row
      and claims it atomically (claimed_at, UPDATE WHERE claimed_at IS NULL):
        (a) exact by dispatch_prompt_id and/or dispatch_description;
        (b) FIFO oldest-first when several candidates are indistinguishable
            AND their material signatures all agree;
        (c) GUARD: divergent signatures -> claim NOTHING, write a critical
            `dispatch_correlation_ambiguous` anomaly + a warning
            harness_event, and let the caller fall back.

Runs against a fresh DB; the writer's own ``_connect`` materializes the real
schema (v43 columns included) from ``gaia/store/schema.sql``.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gaia.store.writer import (
    DISPATCH_CORRELATION_AMBIGUOUS_ANOMALY,
    DISPATCH_CORRELATION_AMBIGUOUS_EVENT,
    claim_dispatch_row,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _birth(db, token: str, **overrides):
    """Birth one row with distinct identity and sensible dispatch defaults."""
    agent_id = valid_agent_id(f"a{token}")
    fields = {
        "agent_name": "gaia-system",
        "kind": "investigation",
        "dispatch_prompt_id": "prompt-1",
        "dispatch_description": "fix the flaky build",
        "dispatch_prompt": "investigate why the build is flaky",
        "dispatch_tool_use_id": f"toolu_{token}",
        "context_anchors": ["stack", "project_identity"],
        "kernel_sections": {
            "role": "primary", "surface": "app_ci_tooling",
            "can_read": ["stack"], "can_write": [],
        },
    }
    fields.update(overrides)
    result = insert_dispatched_handoff(
        f"{agent_id}.{token}cafe", agent_id, WORKSPACE,
        session_id="sess-claim", db_path=db, **fields,
    )
    assert result["created"] is True
    return result["contract_id"]


def _row(db, contract_id):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# (1) enriched birth
# ---------------------------------------------------------------------------

def test_birth_persists_dispatch_coordinates_and_kernel_payload(db):
    contract_id = _birth(db, "111111")
    row = _row(db, contract_id)
    assert row["dispatch_prompt_id"] == "prompt-1"
    assert row["dispatch_tool_use_id"] == "toolu_111111"
    assert row["dispatch_description"] == "fix the flaky build"
    assert row["dispatch_prompt"] == "investigate why the build is flaky"
    assert row["claimed_at"] is None
    assert json.loads(row["context_anchors"]) == ["stack", "project_identity"]
    assert json.loads(row["kernel_sections"])["surface"] == "app_ci_tooling"


def test_birth_without_coordinates_stays_null(db):
    contract_id = _birth(
        db, "222222",
        dispatch_prompt_id=None, dispatch_tool_use_id=None,
        dispatch_description=None, dispatch_prompt=None,
        context_anchors=None, kernel_sections=None,
    )
    row = _row(db, contract_id)
    for column in ("dispatch_prompt_id", "dispatch_tool_use_id",
                   "dispatch_description", "dispatch_prompt",
                   "context_anchors", "kernel_sections", "claimed_at"):
        assert row[column] is None


# ---------------------------------------------------------------------------
# (2a) exact correlation
# ---------------------------------------------------------------------------

def test_claim_exact_by_prompt_id(db):
    contract_id = _birth(db, "333333")
    _birth(db, "444444", dispatch_prompt_id="prompt-OTHER")

    claimed = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    assert claimed is not None
    assert claimed["contract_id"] == contract_id
    assert claimed["claimed_at"] is not None
    assert _row(db, contract_id)["claimed_at"] == claimed["claimed_at"]


def test_claim_exact_by_description_narrows_shared_prompt_id(db):
    """Two same-turn dispatches share prompt_id; description tells them apart."""
    first = _birth(db, "555555", dispatch_description="task A",
                   dispatch_prompt="do A")
    second = _birth(db, "666666", dispatch_description="task B",
                    dispatch_prompt="do B")

    claimed = claim_dispatch_row(
        agent_name="gaia-system",
        dispatch_prompt_id="prompt-1",
        dispatch_description="task B",
        db_path=db,
    )
    assert claimed is not None and claimed["contract_id"] == second
    assert _row(db, first)["claimed_at"] is None


def test_claim_scoped_by_agent_name(db):
    _birth(db, "777777", agent_name="developer")
    mine = _birth(db, "888888", agent_name="gaia-system")

    claimed = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    assert claimed is not None and claimed["contract_id"] == mine


def test_claim_requires_a_correlation_key(db):
    _birth(db, "999999")
    assert claim_dispatch_row(agent_name="gaia-system", db_path=db) is None


def test_claimed_row_is_not_claimable_twice(db):
    _birth(db, "aaaaaa")
    first = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    assert first is not None
    second = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    assert second is None, "claimed_at IS NULL excludes an already-claimed row"


# ---------------------------------------------------------------------------
# (2b) FIFO among identical siblings
# ---------------------------------------------------------------------------

def test_identical_siblings_claim_fifo_oldest_first(db):
    oldest = _birth(db, "bbbbbb")
    newest = _birth(db, "cccccc")

    first_claim = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    second_claim = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    assert first_claim is not None and first_claim["contract_id"] == oldest
    assert second_claim is not None and second_claim["contract_id"] == newest


# ---------------------------------------------------------------------------
# (2·0) exact correlation by dispatch_tool_use_id (plan 65 T7, E1 amendment)
#
# Case (d) of gate 1007 IS test_identical_siblings_claim_fifo_oldest_first
# above: it claims with no dispatch_tool_use_id at all, so layer 0 never
# engages and FIFO resolves exactly as before -- deliberately left untouched.
# ---------------------------------------------------------------------------

def test_claim_by_tool_use_id_wins_over_identical_prompt_id(db):
    """Two identical-looking dispatches, distinct call ids: each call id
    claims its own row even though prompt_id/description tie between them --
    layer 0 resolves before the (a)/(b) ladder ever runs."""
    first = _birth(db, "10a10a", dispatch_tool_use_id="toolu_first")
    second = _birth(db, "10b10b", dispatch_tool_use_id="toolu_second")

    claimed_first = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1",
        dispatch_tool_use_id="toolu_second", db_path=db,
    )
    claimed_second = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1",
        dispatch_tool_use_id="toolu_first", db_path=db,
    )
    assert claimed_first is not None and claimed_first["contract_id"] == second
    assert claimed_second is not None and claimed_second["contract_id"] == first


def test_claim_by_tool_use_id_rejects_double_claim(db):
    """A duplicate start notification for the same call id must not fall
    through to (a) and misclaim an unrelated, still-unclaimed sibling."""
    mine = _birth(db, "20a20a", dispatch_tool_use_id="toolu_dup")
    sibling = _birth(db, "20b20b", dispatch_tool_use_id="toolu_other")

    first = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1",
        dispatch_tool_use_id="toolu_dup", db_path=db,
    )
    second = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1",
        dispatch_tool_use_id="toolu_dup", db_path=db,
    )
    assert first is not None and first["contract_id"] == mine
    assert second is None, "the duplicate call id must decline, not misclaim the sibling"
    assert _row(db, sibling)["claimed_at"] is None


def test_claim_declines_when_callid_unusable_against_callid_born_siblings(db):
    """The claim supplies a call id (0) cannot resolve (stale/unmatched), and
    the survivors it falls back to were themselves born under a call id --
    guessing FIFO here risks binding the wrong sibling, so decline instead."""
    one = _birth(db, "30a30a", dispatch_tool_use_id="toolu_one")
    two = _birth(db, "30b30b", dispatch_tool_use_id="toolu_two")

    claimed = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1",
        dispatch_tool_use_id="toolu_never_born", db_path=db,
    )
    assert claimed is None, "unusable callID against callID-born siblings must decline"
    assert _row(db, one)["claimed_at"] is None
    assert _row(db, two)["claimed_at"] is None


def test_claim_by_tool_use_id_preserves_harness_agent_id_recovery_join(db):
    """The recovery join (stamp_harness_agent_id) stays reachable through a
    layer-0 claim exactly as it does through the legacy ladder."""
    contract_id = _birth(db, "40a40a", dispatch_tool_use_id="toolu_recov")

    claimed = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1",
        dispatch_tool_use_id="toolu_recov", db_path=db,
    )
    assert claimed is not None and claimed["contract_id"] == contract_id

    stamp = stamp_harness_agent_id(contract_id, "harness-run-1", db_path=db)
    assert stamp["status"] == "applied"
    assert _row(db, contract_id)["harness_agent_id"] == "harness-run-1"


# ---------------------------------------------------------------------------
# (2c) divergent-signature guard
# ---------------------------------------------------------------------------

def _events(db, event_type):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM harness_events WHERE type = ?", (event_type,),
        ).fetchall()
    finally:
        con.close()


def _anomalies(db, anomaly_type):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM episode_anomalies WHERE type = ?", (anomaly_type,),
        ).fetchall()
    finally:
        con.close()


def test_divergent_signatures_claim_nothing_and_write_the_anomaly(db):
    """Same correlation keys, materially different dispatches -> refuse."""
    one = _birth(db, "dddddd", dispatch_prompt="materially different goal A")
    two = _birth(db, "eeeeee", dispatch_prompt="materially different goal B")

    claimed = claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    )
    assert claimed is None
    assert _row(db, one)["claimed_at"] is None
    assert _row(db, two)["claimed_at"] is None

    anomalies = _anomalies(db, DISPATCH_CORRELATION_AMBIGUOUS_ANOMALY)
    assert len(anomalies) == 1
    assert anomalies[0]["severity"] == "critical"
    payload = json.loads(anomalies[0]["payload"])
    assert set(payload["candidate_contract_ids"]) == {one, two}

    events = _events(db, DISPATCH_CORRELATION_AMBIGUOUS_EVENT)
    assert len(events) == 1
    assert events[0]["severity"] == "warning"


def test_divergent_kind_also_trips_the_guard(db):
    _birth(db, "f0f0f0", kind="investigation")
    _birth(db, "f1f1f1", kind="memory")

    assert claim_dispatch_row(
        agent_name="gaia-system", dispatch_prompt_id="prompt-1", db_path=db,
    ) is None
    assert len(_anomalies(db, DISPATCH_CORRELATION_AMBIGUOUS_ANOMALY)) == 1
