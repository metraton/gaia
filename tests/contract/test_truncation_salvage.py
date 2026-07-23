"""
AC-12 -- truncation salvage (M5, task T11).

A turn TRUNCATED by the token budget (Claude Code ``stop_reason == "max_tokens"``
-> the adapter's ``STOP_REASON_TRUNCATION`` class) is not the agent's choice to
stop: it was cut off mid-work, so whatever partial contract draft it left on
disk is a SALVAGE candidate, not a violation. The adapter's fast-path rescue
(``ClaudeCodeAdapter._salvage_truncated_draft``) EARLY auto-finalizes that draft
to a ``degraded=true`` row so the work is not lost.

The properties this proves (AC-12 -- "un turno truncado con draft parcial ->
auto-finalize a una fila degraded=true, distinguible de un COMPLETE verificado"):

  * A truncated turn with a partial draft yields EXACTLY ONE row, marked
    ``degraded=true`` with a ``salvaged="truncation"`` marker.
  * That salvaged row is DISTINGUISHABLE from an agent-verified COMPLETE row
    (which carries NO ``degraded`` flag).
  * Salvage keys on the SAME ``contract_id`` (the draft_id resolved from the
    agent's minted agent_id) the T9 hook backstop keys on, so salvage + backstop
    CONVERGE to one row via the writer's ``ON CONFLICT(contract_id) DO NOTHING``
    -- no duplicate, in either arrival order.
  * The salvage is an OPTIMIZATION, never a gate: no draft -> no-op (the T9
    backstop still captures a minimal degraded row); it never raises.
  * It only fires on TRUNCATION -- an ``end_turn`` turn is NOT salvage-marked.
  * The resume hint it surfaces is rendered by view.py's SINGLE renderer
    (``gaia.contract.view.render_resume_hint``) -- never re-inlined in the
    adapter (T14).

Drafts live under an isolated ``GAIA_DATA_DIR``; the DB is a separate isolated
file (either passed via ``task_info['db_path']`` for the direct-method tests, or
the default ``GAIA_DATA_DIR/gaia.db`` the adapter resolves for the end-to-end
tests). The writer materializes the real v28 schema on first connect -- not a
fixture.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# The adapter and the backstop live under hooks/. Put hooks/ on the path at
# import time (mirrors test_exactly_once_and_backstop.py / test_stop_reason_adapter.py).
_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from adapters.claude_code import (  # noqa: E402
    STOP_REASON_TRUNCATION,
    ClaudeCodeAdapter,
    classify_stop_reason,
)
from gaia.contract.drafts import mint_draft_id, save_draft  # noqa: E402
from gaia.contract.view import render_resume_hint  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    agent_contract_handoff_exists,
    finalize_agent_contract_handoff,
)
from modules.agents.handoff_persister import persist_handoff  # noqa: E402

VALID_AGENT_ID = "a1234abcd"
WORKSPACE = "me"


# ---------------------------------------------------------------------------
# Fixtures (mirror test_exactly_once_and_backstop.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    """Isolate the drafts substrate + the default DB, and clear the dispatch id.

    ``GAIA_DATA_DIR`` relocates BOTH the drafts dir (``<data>/contract_drafts``)
    and the default DB (``<data>/gaia.db``) the adapter resolves when task_info
    carries no explicit db_path. ``GAIA_DISPATCH_AGENT`` unset -> the write-guard
    early-returns (allowed), exactly how the hook runs (T8 carry-forward).
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    yield


@pytest.fixture()
def db(tmp_path):
    """Isolated DB path; the writer materializes the real schema on first use."""
    return tmp_path / "gaia.db"


@pytest.fixture()
def default_db(tmp_path):
    """The DB the adapter resolves by default (GAIA_DATA_DIR/gaia.db)."""
    return tmp_path / "gaia_data" / "gaia.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _partial_envelope(plan_status: str = "IN_PROGRESS") -> dict:
    """A partial draft as a truncated turn would leave it: work started, not a
    verified COMPLETE (no verification.result=='pass' terminal)."""
    return {
        "agent_status": {
            "plan_status": plan_status,
            "agent_id": VALID_AGENT_ID,
            "pending_steps": ["finish the write", "verify"],
            "next_action": "continue",
        },
        "evidence_report": {
            "patterns_checked": ["gaia-patterns"],
            "files_checked": ["hooks/adapters/claude_code.py"],
            "commands_run": [], "key_outputs": [], "verbatim_outputs": [],
            "cross_layer_impacts": [], "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _verified_complete_envelope() -> dict:
    return {
        "agent_status": {
            "plan_status": "COMPLETE",
            "agent_id": VALID_AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["ac12"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path | None) -> dict:
    ti = {"agent_id": VALID_AGENT_ID, "agent": "developer", "workspace": WORKSPACE}
    if db_path is not None:
        ti["db_path"] = str(db_path)
    return ti


def _rows(db_path: Path):
    if not db_path.is_file():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT contract_id, agent_id, agent_state, raw_handoff_json "
            "FROM agent_contract_handoffs ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _payload(raw_handoff_json: str) -> dict:
    return json.loads(raw_handoff_json)


def _adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


# ---------------------------------------------------------------------------
# 1. Salvage finalizes a partial draft as degraded=true
# ---------------------------------------------------------------------------

def test_salvage_finalizes_partial_draft_as_degraded(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _partial_envelope("IN_PROGRESS"))

    assert agent_contract_handoff_exists(draft_id, db_path=db) is False

    out = _adapter()._salvage_truncated_draft(
        parsed_contract=None,               # truncated: no valid contract block
        task_info=_task_info(db),
        session_id="sess-salvage",
    )

    assert out is not None, "a partial draft under truncation must be salvaged"
    assert out["contract_id"] == draft_id
    assert out["created"] is True

    rows = _rows(db)
    assert len(rows) == 1, "salvage must leave exactly one row"
    row = rows[0]
    assert row["contract_id"] == draft_id
    payload = _payload(row["raw_handoff_json"])
    assert payload.get("degraded") is True
    assert payload.get("salvaged") == "truncation"
    # A truncated turn never reached a verified terminal state.
    assert row["agent_state"] == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# 2. Salvaged row is DISTINGUISHABLE from an agent-verified COMPLETE
# ---------------------------------------------------------------------------

def test_salvaged_row_distinguishable_from_verified_complete(db):
    # (a) An agent-verified COMPLETE row -- the PRIMARY finalize path.
    complete_draft = mint_draft_id(VALID_AGENT_ID)
    complete_env = _verified_complete_envelope()
    save_draft(complete_draft, complete_env)
    finalize_agent_contract_handoff(
        contract_id=complete_draft,
        agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE,
        task_status="COMPLETE",
        raw_handoff_json=json.dumps(complete_env),
        db_path=db,
    )

    # (b) A salvaged truncated row on a DIFFERENT draft.
    trunc_draft = mint_draft_id(VALID_AGENT_ID)
    save_draft(trunc_draft, _partial_envelope("IN_PROGRESS"))
    # resolve_draft_id(agent_id=...) returns the most-recent draft -> the
    # truncated one (just written). Salvage targets it.
    out = _adapter()._salvage_truncated_draft(
        parsed_contract=None,
        task_info=_task_info(db),
        session_id="sess-distinct",
    )
    assert out["contract_id"] == trunc_draft

    by_id = {r["contract_id"]: _payload(r["raw_handoff_json"]) for r in _rows(db)}
    assert set(by_id) == {complete_draft, trunc_draft}
    # The verified COMPLETE carries NO degraded flag; the salvaged row does.
    assert by_id[complete_draft].get("degraded") is None
    assert by_id[complete_draft].get("salvaged") is None
    assert by_id[trunc_draft].get("degraded") is True
    assert by_id[trunc_draft].get("salvaged") == "truncation"


# ---------------------------------------------------------------------------
# 3. Salvage reuses view.py's SINGLE renderer for the resume hint (T14)
# ---------------------------------------------------------------------------

def test_salvage_reuses_view_render_resume_hint(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _partial_envelope("IN_PROGRESS")
    save_draft(draft_id, envelope)

    out = _adapter()._salvage_truncated_draft(
        parsed_contract=None,
        task_info=_task_info(db),
        session_id="sess-hint",
    )
    # Byte-for-byte the same string the shared renderer produces -- proves the
    # adapter did not re-inline hint text.
    assert out["resume_hint"] == render_resume_hint(draft_id, envelope)


# ---------------------------------------------------------------------------
# 4. No draft -> salvage is a no-op (returns None); T9 remains the floor
# ---------------------------------------------------------------------------

def test_salvage_no_draft_returns_none(db):
    out = _adapter()._salvage_truncated_draft(
        parsed_contract=None,
        task_info=_task_info(db),
        session_id="sess-nodraft",
    )
    assert out is None, "no draft -> nothing to salvage (T9 backstop is the floor)"
    assert _rows(db) == []


# ---------------------------------------------------------------------------
# 5. Convergence with the T9 backstop -- exactly one row, either arrival order
# ---------------------------------------------------------------------------

def test_salvage_then_backstop_one_row(db):
    """Salvage (fast-path) wins first; the T9 backstop then stays passive on the
    SAME contract_id -> exactly one row, keeping the salvage marker."""
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _partial_envelope("IN_PROGRESS")
    save_draft(draft_id, envelope)

    out = _adapter()._salvage_truncated_draft(
        parsed_contract=envelope,
        task_info=_task_info(db),
        session_id="sess-conv-1",
    )
    assert out["created"] is True

    # The T9 backstop now runs on the same turn -> must be a passive no-op.
    persist_handoff(
        parsed_contract=envelope,
        agent_output="",
        task_info=_task_info(db),
        session_id="sess-conv-1",
    )

    rows = _rows(db)
    assert len(rows) == 1, "salvage + backstop must converge to one row"
    payload = _payload(rows[0]["raw_handoff_json"])
    assert payload.get("degraded") is True
    # Salvage got there first -> the surviving row is the salvage-marked one.
    assert payload.get("salvaged") == "truncation"


def test_backstop_then_salvage_one_row(db):
    """If the T9 backstop finalized first, the salvage fast-path sees the row
    exists and is a passive no-op -> still exactly one row, no duplicate."""
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _partial_envelope("IN_PROGRESS")
    save_draft(draft_id, envelope)

    persist_handoff(
        parsed_contract=envelope,
        agent_output="partial",
        task_info=_task_info(db),
        session_id="sess-conv-2",
    )
    assert agent_contract_handoff_exists(draft_id, db_path=db) is True

    out = _adapter()._salvage_truncated_draft(
        parsed_contract=envelope,
        task_info=_task_info(db),
        session_id="sess-conv-2",
    )
    # The salvage still resolves the same draft, but the writer's UPSERT makes
    # it a no-op (created=False) -- the backstop already owns the row.
    assert out is not None
    assert out["contract_id"] == draft_id
    assert out["created"] is False

    rows = _rows(db)
    assert len(rows) == 1, "backstop + salvage must converge to one row"
    assert _payload(rows[0]["raw_handoff_json"]).get("degraded") is True


# ---------------------------------------------------------------------------
# 6. End-to-end through the adapter: salvage ONLY on truncation
# ---------------------------------------------------------------------------

def _subagent_stop_event(adapter, *, stop_reason: str, agent_output: str):
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "sess-e2e",
        "agent_type": "developer",
        "agent_id": VALID_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": agent_output,
        "stop_reason": stop_reason,
        "cwd": "/tmp",
    }
    return adapter.parse_event(json.dumps(payload))


def test_adapter_salvages_on_max_tokens_truncation(default_db):
    """Driving the real SubagentStop lifecycle with stop_reason=max_tokens and a
    partial draft on disk -> the adapter salvages it (degraded + truncation
    marker) and surfaces it in the response."""
    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _partial_envelope("IN_PROGRESS"))

    adapter = _adapter()
    event = _subagent_stop_event(
        adapter, stop_reason="max_tokens",
        agent_output="I was working on the fix and then got cut off mid-sen",
    )
    response = adapter.adapt_subagent_stop(event)

    # The stop_reason was classified as truncation and surfaced (T10 fields).
    assert response.output.get("stop_reason") == "max_tokens"
    assert response.output.get("stop_reason_classification") == STOP_REASON_TRUNCATION
    # The salvage signal is surfaced in the response.
    assert response.output.get("truncation_salvaged") is True
    assert response.output.get("salvage_contract_id") == draft_id

    rows = _rows(default_db)
    salvaged = [r for r in rows if r["contract_id"] == draft_id]
    assert len(salvaged) == 1, "truncation salvage must leave exactly one row"
    payload = _payload(salvaged[0]["raw_handoff_json"])
    assert payload.get("degraded") is True
    assert payload.get("salvaged") == "truncation"


def test_adapter_does_not_salvage_on_end_turn(default_db):
    """An end_turn turn is a genuine stop, not a truncation: the salvage
    fast-path does NOT fire (no truncation marker, no truncation_salvaged
    signal). The T9 backstop still captures the row, but it is NOT salvage-marked
    -- proving the salvage is strictly truncation-gated."""
    assert classify_stop_reason("end_turn") != STOP_REASON_TRUNCATION

    draft_id = mint_draft_id(VALID_AGENT_ID)
    save_draft(draft_id, _partial_envelope("IN_PROGRESS"))

    adapter = _adapter()
    event = _subagent_stop_event(
        adapter, stop_reason="end_turn",
        agent_output="done, but I forgot to emit a contract block",
    )
    response = adapter.adapt_subagent_stop(event)

    assert response.output.get("stop_reason_classification") != STOP_REASON_TRUNCATION
    assert response.output.get("truncation_salvaged") is None

    rows = _rows(default_db)
    for r in rows:
        payload = _payload(r["raw_handoff_json"])
        assert payload.get("salvaged") != "truncation", (
            "end_turn must never produce a truncation-salvaged row"
        )
