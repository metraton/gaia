"""A rejected contract must not become an unbounded retry loop.

The measured defect: the SubagentStop gate returns exit_code=2, the harness
hands the rejection to the SUBAGENT, the subagent repairs and stops again --
and no layer counted the passes. One agent went around ELEVEN times and spent
361k tokens; another, the day before, ten. The rejection message was
byte-identical on pass one and pass eleven, so nothing in it could tell an
agent it was repeating itself, and the relay text it re-read on every pass grew
to 37 KB.

These tests pin the circuit breaker that ends that loop:

  * the FIRST and SECOND rejections still invite a repair (exit 2), each
    carrying its own attempt number;
  * the THIRD ENDS the turn instead of inviting a fourth;
  * and the close is DEGRADED, never a certification -- the contract does not
    come out looking complete, and the trace is queryable through the same
    channels ``gaia defects`` already reads.
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

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.agents import rejected_turn_relay as relay  # noqa: E402


def _circuit():
    """The breaker module, imported lazily.

    Deliberately NOT a module-level import: the two headline tests below assert
    only what an operator can observe through ``adapt_subagent_stop``, so this
    file must still COLLECT on a tree where the breaker does not exist yet. A
    collection error would prove the module is missing; these tests are here to
    prove the BEHAVIOUR is.
    """
    from modules.agents import rejection_circuit

    return rejection_circuit


SUBSTANTIVE = (
    "The diagnosis this turn produced: resolve_minted_agent_id falls back to "
    "the harness agent id, so resolve_draft_id globs a key space no draft "
    "lives in and returns None without failing loudly."
)
HARNESS_AGENT_ID = "aac5be534edc91e44"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.delenv("GAIA_CONTRACT_MAX_REJECTIONS", raising=False)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    yield


MINTED_AGENT_ID = "a" + "b" * 16

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _complete_envelope() -> dict:
    evidence = {k: [] for k in _EVIDENCE_KEYS}
    evidence["verification"] = {
        "method": "suite", "result": "pass", "details": "green",
    }
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": MINTED_AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": evidence,
        "consolidation_report": None,
        "approval_request": None,
    }


def _self_declared_complete_message() -> str:
    """A turn that CLAIMS completion in its text but never finalized its row.

    This is the shape that produces a COMPLETE row through the backstop, and so
    the only shape that can test the hard constraint at all.
    """
    return (
        "El trabajo quedo terminado y la suite verde.\n\n"
        "```agent_contract_handoff\n"
        + json.dumps(_complete_envelope(), indent=2)
        + "\n```"
    )


def _birth_row(db: Path, session_id: str) -> str:
    """A genuinely born dispatch row, left unfinalized -- as a real turn leaves it."""
    from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id

    contract_id = f"{MINTED_AGENT_ID}.{session_id}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=MINTED_AGENT_ID,
        workspace="me",
        session_id=session_id,
        db_path=db,
    )
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db)
    return contract_id


def _reject_once(session_id: str, message: str = SUBSTANTIVE, db: Path | None = None):
    """One full SubagentStop pass of a rejected turn, as the harness drives it."""
    adapter = ClaudeCodeAdapter()
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": session_id,
        "agent_type": "developer",
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": message,
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }
    if db is not None:
        payload["db_path"] = str(db)
    event = adapter.parse_event(json.dumps(payload))
    return adapter.adapt_subagent_stop(event)


# ---------------------------------------------------------------------------
# The acceptance criterion: three consecutive rejections of ONE turn
# ---------------------------------------------------------------------------

def test_third_rejection_ends_the_turn_without_completing_the_contract():
    session = "sess-circuit-three"

    first = _reject_once(session)
    second = _reject_once(session)
    third = _reject_once(session)

    # 1 and 2 -- still invited back to repair.
    assert first.exit_code == 2, "the first rejection must still invite a repair"
    assert first.output["contract_rejected"] is True
    assert second.exit_code == 2, "the second rejection must still invite a repair"
    assert second.output["contract_rejected"] is True

    # 3 -- the turn ENDS. exit_code=2 is the invitation; not raising it is how
    # the loop stops.
    assert third.exit_code == 0, (
        "the third rejection must END the turn, not invite a fourth attempt"
    )
    assert third.output.get("contract_rejected") is not True
    assert third.output["contract_circuit_open"] is True
    assert third.output["contract_rejection_count"] == 3
    assert third.output["contract_rejection_limit"] == 3

    # THE HARD CONSTRAINT: ending a turn is not certifying it.
    assert third.output["contract_closed_degraded"] is True
    assert third.output["contract_complete"] is False, (
        "a degraded close must never make the contract look complete"
    )
    assert third.output["contract_validated"] is False
    assert third.output["status"] == "contract_circuit_open", (
        "the degraded close must not report the ordinary 'metrics_captured' status"
    )
    # And the gate's own last verdict survives the close -- a degraded turn that
    # dropped the reason would be indistinguishable from a clean one.
    assert "[CONTRACT CIRCUIT OPEN]" in third.output["contract_degraded_close_reason"]
    assert "[CONTRACT REJECTED]" in third.output["contract_degraded_close_reason"]


def test_the_counter_counts_this_turn_and_dies_with_it():
    session = "sess-circuit-scope"
    key = relay.preservation_key(session, {"agent_id": HARNESS_AGENT_ID})

    assert _circuit().count(key) == 0, "a clean turn starts at zero"
    _reject_once(session)
    assert _circuit().count(key) == 1
    _reject_once(session)
    assert _circuit().count(key) == 2

    # A DIFFERENT turn is a different count -- the ceiling is per-turn, never
    # a running total across dispatches.
    other = relay.preservation_key("sess-circuit-other", {"agent_id": HARNESS_AGENT_ID})
    assert _circuit().count(other) == 0


def test_an_accepted_turn_clears_its_count():
    key = relay.preservation_key("sess-circuit-reset", {"agent_id": HARNESS_AGENT_ID})
    _circuit().record_rejection(key)
    assert _circuit().count(key) == 1

    _circuit().reset(key)
    assert _circuit().count(key) == 0, "the count dies with the turn"


def test_a_tripped_turn_cannot_restart_the_loop():
    key = relay.preservation_key("sess-circuit-sticky", {"agent_id": HARNESS_AGENT_ID})
    for _ in range(3):
        state = _circuit().record_rejection(key)
    assert state.tripped is True

    again = _circuit().record_rejection(key)
    assert again.tripped is True, "a cut turn must not be able to re-enter the loop"
    assert again.attempt == 3, "and it must not keep counting past the ceiling"


# ---------------------------------------------------------------------------
# The message has to say which attempt this is
# ---------------------------------------------------------------------------

def test_the_second_rejection_message_differs_from_the_first_by_the_attempt_number():
    session = "sess-circuit-message"

    first = _reject_once(session)
    second = _reject_once(session)

    first_reason = first.output["contract_rejection_reason"]
    second_reason = second.output["contract_rejection_reason"]

    assert first_reason != second_reason, (
        "byte-identical rejections are what made the loop invisible to the agent"
    )
    assert "INTENTO 1 DE 3" in first_reason
    assert "INTENTO 2 DE 3" in second_reason
    assert "INTENTO 1 DE 3" not in second_reason

    # And the number is actionable, not decorative: it says how many are left.
    assert "quedan 2" in first_reason
    assert "queda 1" in second_reason


def test_the_ceiling_is_configurable_and_a_broken_override_is_ignored(monkeypatch):
    monkeypatch.setenv("GAIA_CONTRACT_MAX_REJECTIONS", "5")
    assert _circuit().max_rejections() == 5

    # A ceiling of zero would trip every first rejection -- a worse failure
    # than falling back to the policy default.
    monkeypatch.setenv("GAIA_CONTRACT_MAX_REJECTIONS", "0")
    assert _circuit().max_rejections() == _circuit().DEFAULT_MAX_REJECTIONS
    monkeypatch.setenv("GAIA_CONTRACT_MAX_REJECTIONS", "nonsense")
    assert _circuit().max_rejections() == _circuit().DEFAULT_MAX_REJECTIONS


# ---------------------------------------------------------------------------
# The trace an operator reads
# ---------------------------------------------------------------------------

def test_the_tripped_turn_lands_in_the_defect_channels():
    """Both channels ``gaia defects`` merges: the episode anomaly floor and the
    hook event log. Read through the same reader the CLI verb uses."""
    from gaia.store.reader import read_defects

    session = "sess-circuit-trace"
    for _ in range(3):
        response = _reject_once(session)
    assert response.output["contract_circuit_open"] is True

    events = read_defects(
        origin="orchestrator", workspace=None,
        type=_circuit().CIRCUIT_OPEN_EVENT, limit=50,
    )
    assert events, (
        "a degraded close must leave an event an operator can query with "
        "`gaia defects --type=agent.contract_circuit_open`"
    )
    assert events[0]["severity"] == "error"
    assert "NOT complete" in events[0]["message"]

    anomalies = read_defects(
        origin="subagent", workspace=None,
        type=_circuit().CIRCUIT_ANOMALY_TYPE, limit=50,
    )
    assert anomalies, "and a critical anomaly on the episode floor"
    assert anomalies[0]["severity"] == "critical"


def test_a_cut_turn_leaves_no_completed_contract_row(tmp_path):
    """The hard constraint, on the case that can actually produce a COMPLETE row.

    An earlier version of this test drove a message of BARE PROSE. With no
    envelope there is no COMPLETE for anything to record, so it passed by
    construction and proved nothing. The real case is the opposite one: an agent
    that DOES emit a fenced envelope declaring COMPLETE and never finalizes its
    row. The gate rejects it (the row, not the text, is the source of truth) --
    and the last-resort backstop meanwhile records that self-declared COMPLETE
    on its own row, on the FIRST pass. Cutting the turn froze that row as the
    final word while the return value said the contract was not complete.
    """
    import sqlite3

    session = "sess-circuit-substrate"
    db = tmp_path / "gaia_data" / "gaia.db"
    contract_id = _birth_row(db, session)

    for _ in range(3):
        response = _reject_once(session, _self_declared_complete_message(), db=db)
    assert response.output["contract_circuit_open"] is True
    assert response.output["contract_complete"] is False

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r) for r in con.execute(
                "SELECT contract_id, agent_state, cut_reason "
                "FROM agent_contract_handoffs"
            )
        ]
    finally:
        con.close()

    assert any(r["contract_id"] == contract_id for r in rows), (
        "the born row must really exist -- otherwise the gate rejects for the "
        "wrong reason and the case under test never occurs"
    )
    completed = [r for r in rows if r["agent_state"] == "COMPLETE"]
    assert completed == [], (
        "a turn cut by the breaker must not leave a row reading COMPLETE; "
        f"survivors: {completed}"
    )
    assert response.output["contract_row_reconciled"] in ("applied", "not_demotable")


def test_the_demotion_cannot_touch_a_row_the_agent_finalized(tmp_path):
    """The demotion's safety boundary is its predicate, not its caller.

    It reaches only rows carrying a cut mark -- rows closed by a CLOSURE path.
    A row an agent finalized itself has no cut mark and must be untouchable,
    or the reconciliation becomes a way to erase real completions.
    """
    import sqlite3

    from gaia.store.writer import (
        demote_uncertified_completion,
        finalize_agent_contract_handoff,
    )

    db = tmp_path / "gaia_data" / "gaia.db"
    contract_id = f"{MINTED_AGENT_ID}.clean"
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=MINTED_AGENT_ID,
        workspace="me",
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_complete_envelope()),
        session_id="sess-clean",
        db_path=db,
    )

    result = demote_uncertified_completion(contract_id, db_path=db)

    assert result["status"] == "skipped"
    assert result["reason"] == "not_demotable"
    con = sqlite3.connect(str(db))
    try:
        state = con.execute(
            "SELECT agent_state FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()[0]
    finally:
        con.close()
    assert state == "COMPLETE", "a genuine completion must survive untouched"


def test_a_cut_turn_is_not_recorded_as_a_successful_episode(tmp_path):
    """The episode's outcome derives from what the turn CLAIMED about itself, so
    a COMPLETE envelope stored the cut pass as a success."""
    import sqlite3

    session = "sess-circuit-episode"
    db = tmp_path / "gaia_data" / "gaia.db"
    _birth_row(db, session)

    for _ in range(3):
        response = _reject_once(session, _self_declared_complete_message(), db=db)
    assert response.output["contract_circuit_open"] is True

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        outcomes = [
            r["outcome"] for r in con.execute(
                "SELECT outcome FROM episodes ORDER BY rowid"
            )
        ]
    finally:
        con.close()

    assert outcomes, "the turn must have written episodes at all"
    assert outcomes[-1] == "failed", (
        f"the cut pass must not read as a success; outcomes were {outcomes}"
    )


def test_the_counter_never_shares_a_key_with_another_turn():
    """A key that falls back to the agent TYPE is shared by every dispatch of
    that type in the session, and the trip is sticky -- so an innocent turn was
    cut on its first ever rejection."""
    circuit = _circuit()

    shared_shape = {"agent": "developer"}
    assert circuit.counter_key("sess-x", shared_shape) is None, (
        "a payload with no per-dispatch identity must yield NO key at all"
    )
    assert circuit.counter_key("sess-x", {}) is None
    assert circuit.counter_key("sess-x", {"agent_id": ""}) is None
    # task_info_builder substitutes this literal when the payload carries no
    # agent_id, so every unidentified turn arrives wearing the same name.
    assert circuit.counter_key("sess-x", {"agent_id": "unknown"}) is None

    # Two real dispatches in one session are two different keys.
    a = circuit.counter_key("sess-x", {"agent_id": "a" + "1" * 16})
    b = circuit.counter_key("sess-x", {"agent_id": "a" + "2" * 16})
    assert a and b and a != b


def test_an_unidentifiable_turn_is_never_cut_and_says_so(tmp_path):
    """Fail open, loudly: a breaker that cannot tell two turns apart must
    decline to cut either."""
    from gaia.store.reader import read_defects

    adapter = ClaudeCodeAdapter()
    for _ in range(4):
        event = adapter.parse_event(json.dumps({
            "hook_event_name": "SubagentStop",
            "session_id": "sess-circuit-nokey",
            "agent_type": "developer",
            # No harness agent_id -- the shape that used to collapse onto a
            # key shared by every developer turn in the session.
            "agent_transcript_path": "",
            "last_assistant_message": SUBSTANTIVE,
            "stop_reason": "end_turn",
            "cwd": "/tmp",
        }))
        response = adapter.adapt_subagent_stop(event)

    assert response.exit_code == 2, (
        "an unidentifiable turn keeps the ordinary gate; it is never cut"
    )
    assert response.output.get("contract_circuit_open") is not True

    unavailable = read_defects(
        origin="subagent", workspace=None,
        type="contract_rejection_circuit_unavailable", limit=50,
    )
    assert unavailable, (
        "a turn running with no ceiling must be visible, not silently unguarded"
    )
    assert "NOT in force" in unavailable[0]["message"]


def test_the_cut_notice_reaches_the_channels_that_are_read(tmp_path):
    """On exit 0 the harness sends stderr to the debug log -- the model never
    sees it. The notice has to travel by stdout JSON instead."""
    session = "sess-circuit-channels"
    db = tmp_path / "gaia_data" / "gaia.db"
    _birth_row(db, session)

    for _ in range(3):
        response = _reject_once(session, _self_declared_complete_message(), db=db)

    assert response.exit_code == 0
    hook_output = response.output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "SubagentStop"
    assert "CORTADO" in hook_output["additionalContext"], (
        "the model must be told the turn was cut"
    )
    assert "aunque declare COMPLETE" in hook_output["additionalContext"], (
        "and told not to read its last message as a valid close"
    )
    assert "CORTADO" in response.output["systemMessage"], "the user is told too"
    assert "decision" not in response.output, (
        "blocking the stop would restart the very loop the breaker ends"
    )


def test_the_degraded_close_is_announced_on_stderr(monkeypatch, capsys):
    """A degraded close exits 0, so it is the one outcome that could end a turn
    with nothing said. It must still be audible where a rejection is."""
    import io
    from contextlib import redirect_stderr

    import subagent_stop

    class _Response:
        exit_code = 0
        output = {
            "contract_circuit_open": True,
            "contract_complete": False,
            "contract_degraded_close_reason": "[CONTRACT CIRCUIT OPEN] 3 de 3",
        }

    class _Adapter:
        def adapt_subagent_stop(self, _event):
            return _Response()

    monkeypatch.setattr(subagent_stop, "get_adapter", lambda: _Adapter())

    err = io.StringIO()
    with redirect_stderr(err):
        with pytest.raises(SystemExit) as exit_info:
            subagent_stop._handle_subagent_stop(object())

    assert exit_info.value.code == 0, "the turn still ends rather than looping"
    stderr = err.getvalue()
    assert "circuit OPEN" in stderr
    assert "NOT complete" in stderr


def test_the_evidence_of_a_cut_turn_is_not_deleted_by_the_cut():
    """The breaker ends the loop; it must not also destroy what the loop was
    protecting. Clearing the preserved text is the ACCEPTED path's cleanup, and
    a tripped turn is not an accepted one."""
    session = "sess-circuit-evidence"
    for _ in range(3):
        response = _reject_once(session)

    key = relay.preservation_key(session, {"agent_id": HARNESS_AGENT_ID})
    preserved = relay.load(key)
    assert preserved is not None, "the cut must not delete the preserved work"
    assert SUBSTANTIVE in preserved
    assert Path(response.output["preserved_output_path"]).is_file()
    assert response.output["preserved_output_path"] in (
        response.output["contract_degraded_close_reason"]
    ), "and the degraded close must say where the evidence is"


# ---------------------------------------------------------------------------
# The reinjected text stops growing
# ---------------------------------------------------------------------------

def test_the_reinjected_text_is_bounded_but_the_preserved_copy_is_whole():
    """The ceiling belongs on what is RE-READ, never on what is KEPT.

    Capping the file destroyed evidence outright: a single 30030-char turn with
    no accumulation at all came back stored at 20107. This module exists so a
    rejection does not cost the work.
    """
    key = relay.preservation_key("sess-circuit-cap", {"agent_id": HARNESS_AGENT_ID})
    single_turn = "x" * 30030

    outcome = relay.on_rejection(single_turn, key=key, rejection_reason="REPAIR")

    assert relay.load(key) == single_turn, (
        "the preserved copy must be the work, whole and unedited"
    )
    assert outcome["chars"] == len(single_turn)
    assert outcome["inline_truncated"] is True, "but the REINJECTION is bounded"
    assert len(outcome["reason"]) < len(single_turn), (
        "the message the agent re-reads is smaller than the preserved copy"
    )
    assert "NOTHING was discarded" in outcome["reason"], (
        "the marker must state the true cause -- an inline budget, not a loss"
    )


def test_accumulated_text_is_also_preserved_whole():
    key = relay.preservation_key("sess-circuit-accum", {"agent_id": HARNESS_AGENT_ID})
    chunk = "y" * 9000

    for i in range(3):
        outcome = relay.on_rejection(
            f"{chunk}{i}", key=key, rejection_reason="REPAIR",
        )

    stored = relay.load(key)
    assert outcome["chars"] == len(stored)
    assert stored.count(chunk) == 3, "every pass is kept, none dropped"
    assert "y0" in stored and "y2" in stored, (
        "neither the oldest nor the NEWEST pass may be silently discarded"
    )


def test_a_later_attempt_reinjects_less_than_the_first():
    base = relay._MAX_INLINE_CHARS
    assert _circuit().inline_budget(1, base) == base
    assert _circuit().inline_budget(2, base) < base
    assert _circuit().inline_budget(3, base) < _circuit().inline_budget(2, base)
    # Never collapses to a useless sliver -- the point is to shrink the repeat,
    # not to reintroduce the loss the relay exists to prevent.
    assert _circuit().inline_budget(9, base) >= 2000


def test_the_second_rejection_carries_less_text_than_the_first():
    session = "sess-circuit-budget"
    long_output = "y" * 19000

    first = _reject_once(session, long_output)
    second = _reject_once(session, long_output)

    assert len(second.output["contract_rejection_reason"]) < len(
        first.output["contract_rejection_reason"]
    ), "retrying while re-reading everything before it is the measured cost"


# ---------------------------------------------------------------------------
# Nothing fails silently
# ---------------------------------------------------------------------------

def test_a_breaker_that_cannot_count_says_so_and_leaves_the_gate_intact(monkeypatch):
    """The ceiling is an addition to the gate, never a precondition for it.

    A breaker that cannot persist its count degrades to the pre-existing
    behavior -- keep rejecting -- but the turn is then running WITHOUT a
    ceiling, which must be visible rather than inferred from its absence.
    """
    def _boom(*_a, **_k):
        raise OSError("counter volume is read-only")

    monkeypatch.setattr(_circuit(), "_write", _boom)

    key = relay.preservation_key("sess-circuit-broken", {"agent_id": HARNESS_AGENT_ID})
    state = _circuit().record_rejection(key)

    assert state.tripped is False, "an uncounted rejection must not trip the breaker"
    assert state.error, "and the failure must be reported, not swallowed"
    anomaly = _circuit().counter_error_anomaly("developer", state)
    assert anomaly["severity"] == "warning"
    assert "NOT in force" in anomaly["message"]


def test_a_broken_breaker_never_downgrades_a_rejection(monkeypatch):
    """exit_code=2 is driven by the gate. The breaker may end a loop on purpose;
    it must never end one by accident."""
    def _boom(*_a, **_k):
        raise RuntimeError("circuit exploded")

    monkeypatch.setattr(_circuit(), "record_rejection", _boom)

    response = _reject_once("sess-circuit-explodes")

    assert response.exit_code == 2, (
        f"a failure in the breaker downgraded the rejection to exit {response.exit_code}"
    )
    assert response.output["contract_rejected"] is True
    assert response.output["contract_rejection_reason"]


def test_contract_attempts_reports_the_real_count_not_a_constant_zero():
    """``contract_attempts`` used to read a `repair_attempts` key off an
    eleven-field dataclass that has no such field, so it was 0 on every turn --
    including the eleven-rejection one."""
    from modules.agents.response_contract import ResponseContractValidation

    assert "repair_attempts" not in ResponseContractValidation.__dataclass_fields__, (
        "the field the old reader looked for still does not exist"
    )

    session = "sess-circuit-attempts"
    assert _reject_once(session).output["contract_attempts"] == 1
    assert _reject_once(session).output["contract_attempts"] == 2
    assert _reject_once(session).output["contract_attempts"] == 3
