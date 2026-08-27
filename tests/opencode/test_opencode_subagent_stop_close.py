"""Gate 1014 (task 539, T11): the real close that replaces OpenCode's
exit-2 SubagentStop stub.

``dispatch_lifecycle.resolve_close`` is exercised directly (host-neutral):
it is exactly what ``OpenCodeAdapter.adapt_subagent_stop`` now calls, keyed
on ``event.session_id`` as the harness_agent_id -- see
``hooks/adapters/opencode.py::adapt_subagent_stop``.

Named cases (evidence_shape, task_gates.id=1014):
  (a) session.idle with a FINALIZED draft (a valid CLOSED_TURN_PLAN_STATUSES
      verdict already written to the draft, e.g. by 'gaia contract
      set/fill', even though the row itself is still DISPATCHED because the
      process died before running 'gaia contract finalize') -> clean
      terminal, cut_reason cleared.
  (b) session.idle AFTER session.error already closed the row -> covered:
      idle is a no-op, the row stays byte-identical to what error left.
  (c) a child with ZERO tool calls -- bound ONLY via message.part.updated
      (T10's bind_harness_child_session), no PreToolUse/PostToolUse ever
      recorded -- closes on session.idle alone.
  (d) a draft with no terminal agent_state (absent, or still IN_PROGRESS)
      -> CUT with cut_reason=CUT_REASON_BACKSTOP_CAPTURE, never left
      DISPATCHED/hung.
  (e) the tests that assumed OpenCode's exit-2 stub are updated in the SAME
      change: see tests/opencode/test_lifecycle_transport_gate.py (updated,
      commit c771bf7) -- no OpenCode-specific test ever asserted the stub's
      literal exit-2/contract_valid=False shape (confirmed by grep, zero
      hits), so the only test carrying the OLD behavior's assumption was
      that lifecycle-transport gate, already rewritten to describe this
      change instead of the acknowledged/no-op placeholder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from gaia.contract.drafts import save_draft
from gaia.state import CUT_REASON_BACKSTOP_CAPTURE
from gaia.store.writer import (
    bind_harness_child_session,
    claim_dispatch_row,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"


@pytest.fixture(autouse=True)
def _isolated_gaia_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return tmp_path


def _db(tmp_path) -> Path:
    return tmp_path / "gaia.db"


def _birth_and_bind(db, token: str, harness_agent_id: str) -> str:
    """Mirrors T9's Task-dispatch claim + T10's message.part.updated bind --
    the exact state a session lifecycle event always finds a dispatched
    child's row in, with or without any tool call ever running."""
    agent_id = valid_agent_id(f"a{token}")
    result = insert_dispatched_handoff(
        f"{agent_id}.{token}cafe", agent_id, WORKSPACE,
        session_id=None, db_path=db,
        agent_name="gaia-system", kind="investigation",
        dispatch_tool_use_id=f"call-{token}",
    )
    contract_id = result["contract_id"]
    claim_dispatch_row(dispatch_tool_use_id=f"call-{token}", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id=f"call-{token}", harness_agent_id=harness_agent_id,
        db_path=db,
    )
    return contract_id


def _row(db, contract_id):
    import sqlite3

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
# (a) session.idle with a finalized draft -> clean terminal, cut_reason cleared
# ---------------------------------------------------------------------------

def test_case_a_idle_with_finalized_draft_closes_clean_with_cut_reason_cleared(tmp_path):
    from modules.agents.dispatch_lifecycle import resolve_close

    db = _db(tmp_path)
    contract_id = _birth_and_bind(db, "aaa111", "child-session-A")
    save_draft(contract_id, {
        "agent_status": {"agent_state": "COMPLETE", "pending_steps": [], "next_action": "done"},
        "evidence_report": {"verification": {"result": "pass"}},
    })

    outcome = resolve_close(harness_agent_id="child-session-A", db_path=db)

    assert outcome == {
        "status": "closed", "contract_id": contract_id,
        "agent_state": "COMPLETE", "cut_reason": None,
    }
    row = _row(db, contract_id)
    assert row["agent_state"] == "COMPLETE"
    assert row["cut_reason"] is None


# ---------------------------------------------------------------------------
# (b) session.idle AFTER session.error already closed the row -> covered (no-op)
# ---------------------------------------------------------------------------

def test_case_b_idle_after_error_is_a_no_op_covered(tmp_path):
    from modules.agents.dispatch_lifecycle import resolve_close

    db = _db(tmp_path)
    contract_id = _birth_and_bind(db, "bbb222", "child-session-B")
    # No draft at all: session.error arrives first and cuts the row.
    error_outcome = resolve_close(harness_agent_id="child-session-B", db_path=db)
    assert error_outcome == {
        "status": "closed", "contract_id": contract_id,
        "agent_state": "IN_PROGRESS", "cut_reason": CUT_REASON_BACKSTOP_CAPTURE,
    }
    row_after_error = _row(db, contract_id)

    # session.idle for the SAME session arrives next.
    idle_outcome = resolve_close(harness_agent_id="child-session-B", db_path=db)

    assert idle_outcome == {"status": "already_closed", "contract_id": contract_id}
    row_after_idle = _row(db, contract_id)
    assert row_after_idle["agent_state"] == row_after_error["agent_state"]
    assert row_after_idle["cut_reason"] == row_after_error["cut_reason"]
    assert row_after_idle["raw_handoff_json"] == row_after_error["raw_handoff_json"]


# ---------------------------------------------------------------------------
# (c) a zero-tool child -- bound only via message.part.updated -- still closes
# ---------------------------------------------------------------------------

def test_case_c_zero_tool_child_closes_via_message_updated_plus_idle(tmp_path):
    from modules.agents.dispatch_lifecycle import resolve_close

    db = _db(tmp_path)
    # _birth_and_bind is EXACTLY T9's claim (Task PreToolUse) + T10's bind
    # (message.part.updated) -- no PreToolUse/PostToolUse for any OTHER tool
    # is ever recorded for this row, which is precisely "zero tools".
    contract_id = _birth_and_bind(db, "ccc333", "child-session-C")
    assert _row(db, contract_id)["harness_agent_id"] == "child-session-C"

    outcome = resolve_close(harness_agent_id="child-session-C", db_path=db)

    assert outcome["status"] == "closed"
    assert outcome["contract_id"] == contract_id
    assert _row(db, contract_id)["agent_state"] != "DISPATCHED"


# ---------------------------------------------------------------------------
# (d) no terminal agent_state in the draft -> CUT, never a hung row
# ---------------------------------------------------------------------------

def test_case_d_no_terminal_draft_state_cuts_with_backstop_capture_never_hung(tmp_path):
    from modules.agents.dispatch_lifecycle import resolve_close

    db = _db(tmp_path)
    contract_id = _birth_and_bind(db, "ddd444", "child-session-D")
    save_draft(contract_id, {
        "agent_status": {"agent_state": "IN_PROGRESS", "pending_steps": ["still working"]},
    })

    outcome = resolve_close(harness_agent_id="child-session-D", db_path=db)

    assert outcome == {
        "status": "closed", "contract_id": contract_id,
        "agent_state": "IN_PROGRESS", "cut_reason": CUT_REASON_BACKSTOP_CAPTURE,
    }
    row = _row(db, contract_id)
    assert row["agent_state"] != "DISPATCHED"
    assert row["cut_reason"] == CUT_REASON_BACKSTOP_CAPTURE


def test_case_d_variant_missing_draft_also_cuts_never_hung(tmp_path):
    from modules.agents.dispatch_lifecycle import resolve_close

    db = _db(tmp_path)
    contract_id = _birth_and_bind(db, "eee555", "child-session-E")
    # No save_draft call at all: load_draft returns None.

    outcome = resolve_close(harness_agent_id="child-session-E", db_path=db)

    assert outcome["status"] == "closed"
    assert outcome["cut_reason"] == CUT_REASON_BACKSTOP_CAPTURE
    assert _row(db, contract_id)["agent_state"] != "DISPATCHED"


# ---------------------------------------------------------------------------
# (e) the tests that assumed the exit-2 stub are updated in the SAME change
# ---------------------------------------------------------------------------

def test_case_e_no_opencode_specific_test_ever_asserted_the_old_exit2_stub():
    """Confirms the (e) premise this gate names: grepping the stub's literal
    message and OpenCodeAdapter().adapt_subagent_stop call sites across
    tests/ returns nothing, so the actual update this gate requires already
    landed in tests/opencode/test_lifecycle_transport_gate.py (commit
    c771bf7), which DID encode the old acknowledged/no-op behavior for
    session.idle/error/deleted and was rewritten to describe the pending
    (now applied) close instead."""
    tests_root = _ROOT / "tests"
    this_file = Path(__file__).resolve()
    stub_message = "OpenCode contract lifecycle is not wired"
    hits = []
    for path in tests_root.rglob("*.py"):
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if stub_message in text or "OpenCodeAdapter().adapt_subagent_stop" in text:
            hits.append(str(path))
    assert hits == []
