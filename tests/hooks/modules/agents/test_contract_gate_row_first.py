"""Row-only SubagentStop gate (source-of-truth migration, step 2).

Step 1 inverted the gate: the turn's own ``agent_contract_handoffs`` row became
authoritative whenever it was reachable and cleanly finalized, with the fenced
``agent_contract_handoff`` block in the final message kept as a fallback. This
step removes the fallback. ``resolve_subagent_stop_gate``
(hooks/adapters/claude_code.py) no longer accepts an envelope from the response
text at all: it reads the persisted row, and a turn whose row is unreachable or
unfinalized fails the close on that basis alone.

These tests exercise the REAL writer functions
(``insert_dispatched_handoff``, ``finalize_agent_contract_handoff``,
``dispatch_row_for_identity``) against a real, isolated SQLite database --
never a mock of the gate or the store.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[4] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[4])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    GATE_SOURCE_ROW,
    GATE_SOURCE_ROW_MISSING,
    GATE_SOURCE_ROW_UNFINALIZED,
    STOP_REASON_TRUNCATION,
    STOP_REASON_VIOLATION,
    resolve_subagent_stop_gate,
)
from gaia.store.writer import (  # noqa: E402
    dispatch_row_for_identity,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("row-first-gate")
SESSION_ID = "sess-row-first"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _evidence() -> dict:
    return {k: [] for k in _EVIDENCE_KEYS}


def _complete_envelope(agent_id: str = AGENT_ID) -> dict:
    env = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }
    env["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": "suite green",
    }
    return env


def _missing_next_action_envelope() -> dict:
    """A malformation that does NOT touch agent_id -- unlike a mismatched
    agent_id, this can genuinely reach a persisted row: `gaia contract
    finalize`'s identity-coherence check (bin/cli/contract.py) only compares
    agent_status.agent_id against the draft's own prefix, so a row missing
    next_action is finalizable while still being MISSING_FIELD-invalid."""
    env = _complete_envelope()
    del env["agent_status"]["next_action"]
    return env


def _birth_row(db_path: Path, *, contract_suffix: str, session_id: str = SESSION_ID) -> str:
    """Birth a real DISPATCHED row via the production writer, return its
    contract_id."""
    contract_id = f"{AGENT_ID}.{contract_suffix}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=session_id,
        db_path=db_path,
    )
    return contract_id


def _finalize_row(db_path: Path, contract_id: str, envelope: dict, *, session_id: str = SESSION_ID) -> None:
    """Converge a born row via the production finalize writer -- the SAME
    call `gaia contract finalize` makes, clearing cut_reason to NULL."""
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=envelope["agent_status"]["agent_id"],
        workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope),
        session_id=session_id,
        db_path=db_path,
    )


def _resolved_row(db_path: Path, session_id: str = SESSION_ID) -> dict:
    """The row a real ADOPTED-lane lookup would resolve -- the exact shape
    ``ClaudeCodeAdapter._resolve_dispatch_row`` hands the gate."""
    row = dispatch_row_for_identity(session_id, AGENT_ID, db_path=db_path)
    assert row is not None, "test setup must have birthed a row first"
    return row


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    yield


@pytest.fixture()
def db(tmp_path) -> Path:
    return tmp_path / "gaia.db"


# ---------------------------------------------------------------------------
# 1. A cleanly-finalized, well-formed row passes on its own evidence.
# ---------------------------------------------------------------------------

def test_row_finalized_well_formed_passes(db):
    contract_id = _birth_row(db, contract_suffix="case1")
    _finalize_row(db, contract_id, _complete_envelope())
    bound_row = _resolved_row(db)

    verdict, source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_ROW
    assert verdict.rejected is False


def test_gate_refuses_an_envelope_from_the_response_text(db):
    """The retirement, stated as a signature: there is no longer a parameter
    through which the final message's fenced block can reach the gate. This
    is the assertion that replaces step 1's 'the row outranks the fence'
    cases -- outranking implied the fence was still an input."""
    contract_id = _birth_row(db, contract_suffix="case1b")
    _finalize_row(db, contract_id, _complete_envelope())
    bound_row = _resolved_row(db)

    with pytest.raises(TypeError):
        resolve_subagent_stop_gate(
            parsed_contract=_complete_envelope(),
            agent_type="gaia-system",
            plan_task_id=None,
            stop_reason_classification=STOP_REASON_VIOLATION,
            ramp_enabled=True,
            bound_dispatch_row=bound_row,
            db_path=str(db),
        )


# ---------------------------------------------------------------------------
# 2. A row that exists but was never cleanly finalized rejects.
# ---------------------------------------------------------------------------

def test_row_unfinalized_rejects(db):
    _birth_row(db, contract_suffix="case2")  # never finalized -- stays DISPATCHED
    bound_row = _resolved_row(db)

    verdict, source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_ROW_UNFINALIZED
    assert verdict.rejected is True
    assert "never cleanly closed" in verdict.rejection_reason
    assert "gaia contract finalize" in verdict.rejection_reason


def test_row_unfinalized_excused_by_truncation(db):
    """The one carve-out, unchanged in effect but no longer routed through the
    fence: a max_tokens truncation is not the agent's violation, so an
    unfinalized row does not reject. The verdict is now produced directly,
    marked salvaged_truncation, with the row still named as the source."""
    _birth_row(db, contract_suffix="case2b")
    bound_row = _resolved_row(db)

    verdict, source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_TRUNCATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_ROW_UNFINALIZED
    assert verdict.rejected is False
    assert verdict.salvaged_truncation is True


# ---------------------------------------------------------------------------
# 3. A finalized but malformed row rejects on its own content.
# ---------------------------------------------------------------------------

def test_malformed_row_rejects(db):
    contract_id = _birth_row(db, contract_suffix="case3")
    _finalize_row(db, contract_id, _missing_next_action_envelope())
    bound_row = _resolved_row(db)

    verdict, source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_ROW
    assert verdict.rejected is True
    from gaia.contract.validator import FormErrorCode
    codes = {a["code"] for a in verdict.anomalies}
    assert FormErrorCode.MISSING_FIELD.value in codes


# ---------------------------------------------------------------------------
# 4. No dispatch row reachable at all -- formerly the fence's branch, now a
#    rejection in its own right.
# ---------------------------------------------------------------------------

def test_no_row_rejects(db):
    verdict, source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=None,
        db_path=str(db),
    )
    assert source == GATE_SOURCE_ROW_MISSING
    assert verdict.rejected is True
    assert "No persisted contract row" in verdict.rejection_reason
    codes = {a["code"] for a in verdict.anomalies}
    assert "ROW_NOT_FOUND" in codes


def test_no_row_names_the_repair_command(db):
    """A rejection has to be recoverable, or it is a dead end rather than a
    failed close: the message must name finalize (for a turn that was given a
    contract) and init (for one that never was)."""
    verdict, _source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=None,
        db_path=str(db),
    )
    assert "gaia contract finalize" in verdict.rejection_reason
    assert "gaia contract init" in verdict.rejection_reason


def test_no_row_excused_by_truncation(db):
    """A harness cut that left no reachable row is still not the agent's
    violation. Before the retirement this case reached the fence, which was
    itself truncation-aware; the excuse now lives at the gate."""
    verdict, source = resolve_subagent_stop_gate(
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_TRUNCATION,
        ramp_enabled=True,
        bound_dispatch_row=None,
        db_path=str(db),
    )
    assert source == GATE_SOURCE_ROW_MISSING
    assert verdict.rejected is False
    assert verdict.salvaged_truncation is True
