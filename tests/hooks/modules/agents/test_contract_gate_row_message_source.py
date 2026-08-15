"""The gate's rejection message must say WHERE the envelope was read from.

``gaia.contract.validator.validate_form`` serves two callers with different
sources. The SubagentStop gate (``resolve_subagent_stop_gate``,
hooks/adapters/claude_code.py) reads the turn's persisted
``agent_contract_handoffs`` row and passes ``source="row"``; the CLI's
validate-on-write reads an envelope handed to it directly and keeps the
default ``source="declaration"``. Its canonical repair message used to be
worded as if the envelope always came from the agent's final response text
("your response must carry an agent_contract_handoff envelope"), which
collapsed two distinct failure modes into one wrong sentence. This module
proves the message forks on source -- at the unit level for both, and end to
end through the gate for the row.
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
    STOP_REASON_VIOLATION,
    resolve_subagent_stop_gate,
)
from gaia.contract.validator import (  # noqa: E402
    CANONICAL_REPAIR_MESSAGE,
    ROW_ENVELOPE_REPAIR_MESSAGE,
    validate_form,
)
from gaia.store.writer import dispatch_row_for_identity, insert_dispatched_handoff  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("row-message-source")
SESSION_ID = "sess-row-message-source"

# A phrase that only makes sense when no row was the SOURCE -- must never
# appear in a row-sourced rejection, which names the row it read.
_DECLARATION_ONLY_PHRASE = "this turn's contract envelope"
# A phrase that only makes sense when the SOURCE was the persisted row --
# must never appear in a declaration-sourced rejection.
_ROW_ONLY_PHRASE = "dispatch row"


def _birth_row(db_path: Path, *, contract_suffix: str) -> str:
    contract_id = f"{AGENT_ID}.{contract_suffix}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db_path,
    )
    return contract_id


def _finalize_with_raw_json(
    db_path: Path, contract_id: str, raw_handoff_json: str,
) -> None:
    """Converge a row directly with a HAND-WRITTEN raw_handoff_json string,
    bypassing the CLI's own JSON-validity check -- the shape a genuinely
    corrupted persisted draft would take (disk/DB-level corruption, a
    partial write race, ...), which is what this module's central case
    exercises."""
    from gaia.store.writer import finalize_agent_contract_handoff

    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=raw_handoff_json,
        session_id=SESSION_ID,
        db_path=db_path,
    )


def _resolved_row(db_path: Path) -> dict:
    row = dispatch_row_for_identity(SESSION_ID, AGENT_ID, db_path=db_path)
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
# Unit level (gaia.contract.validator): the two sources must never share a
# rejection message.
# ---------------------------------------------------------------------------

def test_declaration_source_none_envelope_keeps_the_original_wording():
    """source="declaration" (the default, unchanged) still reads as "your
    response" and reuses CANONICAL_REPAIR_MESSAGE byte-for-byte."""
    result = validate_form(None)

    assert result.ok is False
    detail = result.error_summary()
    assert _ROW_ONLY_PHRASE not in detail
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE
    assert _DECLARATION_ONLY_PHRASE in result.repair_message
    assert _ROW_ONLY_PHRASE not in result.repair_message


def test_row_source_none_envelope_names_the_row_not_the_response():
    """source="row" must say the ROW failed to parse, not the response."""
    result = validate_form(None, source="row")

    assert result.ok is False
    detail = result.error_summary()
    assert _ROW_ONLY_PHRASE in detail or "raw_handoff_json" in detail
    assert _DECLARATION_ONLY_PHRASE not in detail
    assert result.repair_message == ROW_ENVELOPE_REPAIR_MESSAGE
    assert result.repair_message != CANONICAL_REPAIR_MESSAGE


def test_row_source_malformed_dict_also_gets_the_row_repair_message():
    """The fix is not limited to a totally-absent envelope: a row that IS a
    dict but fails a field check (e.g. a divergent/incomplete persisted
    draft) must ALSO get ROW_ENVELOPE_REPAIR_MESSAGE, not the declaration
    one -- the wording forks on source, not on which check fired."""
    malformed = {"agent_status": {"agent_state": "COMPLETE"}}  # missing agent_id, next_action

    result = validate_form(malformed, source="row")

    assert result.ok is False
    assert result.repair_message == ROW_ENVELOPE_REPAIR_MESSAGE
    assert _DECLARATION_ONLY_PHRASE not in result.repair_message


# ---------------------------------------------------------------------------
# End to end through the row-first gate: a row that IS reachable and cleanly
# finalized, but whose raw_handoff_json is corrupted JSON, must reject with a
# message that says the ROW failed, never "your response must carry".
# ---------------------------------------------------------------------------

def test_gate_rejection_for_unreadable_finalized_row_names_the_row(db):
    contract_id = _birth_row(db, contract_suffix="corrupt")
    _finalize_with_raw_json(db, contract_id, "{this is not valid json")
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
    assert _ROW_ONLY_PHRASE in verdict.rejection_reason
    assert _DECLARATION_ONLY_PHRASE not in verdict.rejection_reason


def test_gate_rejection_with_no_row_never_names_the_response(db):
    """Inverted by the fence retirement. This case used to fall to the fence
    and therefore keep the declaration wording ("your response must carry an
    agent_contract_handoff envelope"). With the fence gone the gate has no
    envelope from the response text to fail on, so a no-row rejection must
    point at the missing ROW instead -- telling the agent to fix its response
    text would now be advice it cannot act on."""
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
    assert _DECLARATION_ONLY_PHRASE not in verdict.rejection_reason
    assert "No persisted contract row" in verdict.rejection_reason
