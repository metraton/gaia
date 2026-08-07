"""Row-first SubagentStop gate (source-of-truth inversion, step 1).

Brief context: today the gate parses ONLY the fenced ``agent_contract_handoff``
block out of the agent's final message; the persisted
``agent_contract_handoffs`` row is a parallel, unconsulted record. Measured
consequences: a well-formed fence passes the gate even when the agent never
ran ``gaia contract finalize`` at all, and a fence can silently diverge from
the row because the fence is retyped from memory while the row is the actual
sequence of ``gaia contract`` calls the turn ran.

``resolve_subagent_stop_gate`` (hooks/adapters/claude_code.py) inverts the
source of truth: the turn's OWN dispatch row is looked up first; when it was
cleanly closed by the agent's own finalize, ITS persisted envelope decides
pass/reject through the identical core (``evaluate_contract_gate`` ->
``gaia.contract.crosscheck.validate`` + ``_blind_verification_required``) the
fence used to go through alone. The fence remains the backup path, used only
when no dispatch row is reachable at all (or one exists unfinalized but the
stop was a harness truncation, which is not the agent's violation).

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
    GATE_SOURCE_FENCE,
    GATE_SOURCE_ROW,
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


def _malformed_agent_id_envelope() -> dict:
    """A COMPLETE envelope, verification-passing, EXCEPT agent_id does not
    match ^a[0-9a-f]{16,}$ -- AGENT_ID_FORMAT rejects it under full-verdict."""
    env = _complete_envelope()
    env["agent_status"]["agent_id"] = "BADID"
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
# 1. A cleanly-finalized, well-formed row passes with NO fence at all.
# ---------------------------------------------------------------------------

def test_row_finalized_well_formed_passes_with_no_fence(db):
    contract_id = _birth_row(db, contract_suffix="case1")
    _finalize_row(db, contract_id, _complete_envelope())
    bound_row = _resolved_row(db)

    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=None,  # no fence at all -- absent, not merely incomplete
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_ROW
    assert verdict.rejected is False


def test_row_finalized_well_formed_passes_even_with_incomplete_fence(db):
    """The SAME property, with a fence present but incomplete -- absence and
    incompleteness are both irrelevant once the row is authoritative."""
    contract_id = _birth_row(db, contract_suffix="case1b")
    _finalize_row(db, contract_id, _complete_envelope())
    bound_row = _resolved_row(db)

    incomplete_fence = {"agent_status": {"agent_state": "COMPLETE"}}  # no agent_id, no next_action

    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=incomplete_fence,
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_ROW
    assert verdict.rejected is False


# ---------------------------------------------------------------------------
# 2. A row that exists but was never cleanly finalized must NOT pass silently,
#    however perfect the fence looks.
# ---------------------------------------------------------------------------

def test_row_unfinalized_rejects_despite_a_perfect_fence(db):
    _birth_row(db, contract_suffix="case2")  # never finalized -- stays DISPATCHED
    bound_row = _resolved_row(db)

    perfect_fence = _complete_envelope()

    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=perfect_fence,
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


def test_row_unfinalized_excused_by_truncation_falls_back_to_fence(db):
    """The one carve-out: a max_tokens truncation is not the agent's
    violation, so an unfinalized row does not itself reject -- the fence
    (already truncation-aware) decides instead, exactly as before this
    migration."""
    _birth_row(db, contract_suffix="case2b")
    bound_row = _resolved_row(db)

    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=_malformed_agent_id_envelope(),
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_TRUNCATION,
        ramp_enabled=True,
        bound_dispatch_row=bound_row,
        db_path=str(db),
    )

    assert source == GATE_SOURCE_FENCE
    assert verdict.rejected is False
    assert verdict.salvaged_truncation is True


# ---------------------------------------------------------------------------
# 3. Row and fence diverge -> the row wins, in both directions.
# ---------------------------------------------------------------------------

def test_divergent_malformed_row_rejects_despite_well_formed_fence(db):
    """The row is finalized but MALFORMED (missing next_action); the fence is
    a flawless COMPLETE that would have passed on its own. The row still
    wins: reject."""
    contract_id = _birth_row(db, contract_suffix="case3")
    _finalize_row(db, contract_id, _missing_next_action_envelope())
    bound_row = _resolved_row(db)

    flawless_fence = _complete_envelope()

    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=flawless_fence,
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
# 4. Backup path: no dispatch row reachable at all -- fence decides, both ways.
# ---------------------------------------------------------------------------

def test_no_row_falls_back_to_fence_and_accepts_a_valid_one(db):
    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=_complete_envelope(),
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=None,
        db_path=str(db),
    )
    assert source == GATE_SOURCE_FENCE
    assert verdict.rejected is False


def test_no_row_falls_back_to_fence_and_rejects_a_malformed_one(db):
    verdict, source = resolve_subagent_stop_gate(
        parsed_contract=_malformed_agent_id_envelope(),
        agent_type="gaia-system",
        plan_task_id=None,
        stop_reason_classification=STOP_REASON_VIOLATION,
        ramp_enabled=True,
        bound_dispatch_row=None,
        db_path=str(db),
    )
    assert source == GATE_SOURCE_FENCE
    assert verdict.rejected is True
