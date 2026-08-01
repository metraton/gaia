"""Fail-closed identity: who is asking to close a task, and what that costs them.

Matchable as one suite::

    pytest tests/ -k producer_cannot_close_own_task -q

Every test here carries that phrase so the whole file runs as one selection,
following the naming convention of tests/test_task_close_condition.py.

Two surfaces, one semantics:

  * ``gaia.state.task_closure_identity`` -- the pure decision with identity
    folded in (no DB, no env, no I/O), exercised as an EXHAUSTIVE truth table.
  * ``gaia.store.writer.set_task_status`` -- the single writer of
    ``tasks.status``, against a real disposable sqlite substrate, where the
    binding rows and ``GAIA_DISPATCH_AGENT`` are real rather than simulated.

The truth table is the point of the file, and it is exhaustive BY CONSTRUCTION
rather than by counting: the declared table's keys are asserted equal to the
full cartesian product of the three input axes, so a cell nobody wrote is a
failing test rather than a silent fallthrough. Under a fail-closed regime an
unspecified cell resolves to whatever the language does next, which is exactly
the failure this shape prevents.

What is asserted goes past the enumerated cases, because the properties are
stronger than the cells:

  * THE PRODUCER REFUSAL IS ABSOLUTE. Not merely "refused without an override" --
    refused WITH a valid one, across every gate state including all-passing.
  * THE ABSENCE OF A BINDING IS NOT AN APPROVAL. The unlinked column grants
    exactly what the distinct-caller column grants and never more, which is the
    property the branch exists to hold. It is asserted as an equality between
    the two columns rather than case by case, so a future divergence fails here.
  * THERE IS ONE OVERRIDE PATH, NOT TWO. The unlinked refusal is lifted by the
    same flags, validated by the same validator, and recorded through the same
    channel as any other -- asserted by driving it through the writer and
    reading the persisted event back.
  * THE REFUSAL SAYS WHICH FACT IS MISSING. Gates outstanding and no binding are
    distinguishable in the message, because they demand different corrections.
  * A REFUSAL LEAVES NOTHING BEHIND -- no status change and no audit record.
  * SKIPPED AND REOPEN STAY EXEMPT, for the producer too: neither asserts the
    work was verified, so neither is the producer certifying anything.
"""

from __future__ import annotations

import itertools
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state.task_closure import (  # noqa: E402
    APPROVING_GATE_STATUS,
    derive_gate_verdict,
)
from gaia.state.task_closure_condition import TaskClosureBlocked  # noqa: E402
from gaia.state.task_closure_event import (  # noqa: E402
    HUMAN_ACTOR,
    MISSING_REASON_MESSAGE,
    TASK_CLOSE_OVERRIDE_EVENT,
)
from gaia.state.task_closure_identity import (  # noqa: E402
    ProducerStanding,
    classify_producer_standing,
    decide_closure_under_identity,
    producer_agent_names,
    unlinked_denial_clause,
)

_BRIEF = "producer-identity-brief"
_WORKSPACE = "me"
_ORDER = 1
_REASON = "the verifier fleet is down; closing under protest with the record"

# The agent the task is dispatched to in the seeded fixtures, and one that is
# not. Real fleet names, because the writer's own guards resolve identities
# against the seeded fleet.
_PRODUCER = "developer"
_OTHER_AGENT = "gaia-verifier"


# ---------------------------------------------------------------------------
# The three input axes
# ---------------------------------------------------------------------------

# Gate states, named by what they mean rather than by their row contents, and
# spanning the three cases the verdict primitive distinguishes: complete
# positive evidence, incomplete evidence, and no evidence at all.
_GATE_STATES: dict[str, tuple[str, ...]] = {
    "all_approved": (APPROVING_GATE_STATUS, APPROVING_GATE_STATUS),
    "some_unapproved": (APPROVING_GATE_STATUS, "pending"),
    "zero_gates": (),
}

# Override states. A malformed reason is deliberately NOT an axis value: it is
# an argument error rather than a cell of the predicate, and it has its own test
# below.
_OVERRIDE_STATES: dict[str, object] = {
    "absent": None,
    "present_with_reason": _REASON,
}

_STANDINGS = tuple(ProducerStanding)


# ---------------------------------------------------------------------------
# The truth table, declared cell by cell
# ---------------------------------------------------------------------------
#
# Key: (gate state, standing, override state) -> (permitted, override_used).
# Every one of the 3 x 3 x 2 cells is written out explicitly. The verdict of a
# cell is a design decision, so it is stated rather than computed -- a table
# derived from the implementation would agree with any implementation.

_TRUTH_TABLE: dict[tuple[str, ProducerStanding, str], tuple[bool, bool]] = {
    # A BOUND PRODUCER IS REFUSED IN ALL SIX OF ITS CELLS. Passing gates do not
    # help (the producer is the one who would have recorded them) and neither
    # does an override (an override that could would make the producer its own
    # verifier).
    ("all_approved",    ProducerStanding.BOUND_PRODUCER, "absent"):              (False, False),
    ("all_approved",    ProducerStanding.BOUND_PRODUCER, "present_with_reason"): (False, False),
    ("some_unapproved", ProducerStanding.BOUND_PRODUCER, "absent"):              (False, False),
    ("some_unapproved", ProducerStanding.BOUND_PRODUCER, "present_with_reason"): (False, False),
    ("zero_gates",      ProducerStanding.BOUND_PRODUCER, "absent"):              (False, False),
    ("zero_gates",      ProducerStanding.BOUND_PRODUCER, "present_with_reason"): (False, False),

    # A CALLER WHO IS NOT THE NAMED PRODUCER falls to the disjunction untouched:
    # evidence closes it, or a stated reason does, or nothing does.
    ("all_approved",    ProducerStanding.DISTINCT_FROM_PRODUCER, "absent"):              (True,  False),
    ("all_approved",    ProducerStanding.DISTINCT_FROM_PRODUCER, "present_with_reason"): (True,  False),
    ("some_unapproved", ProducerStanding.DISTINCT_FROM_PRODUCER, "absent"):              (False, False),
    ("some_unapproved", ProducerStanding.DISTINCT_FROM_PRODUCER, "present_with_reason"): (True,  True),
    ("zero_gates",      ProducerStanding.DISTINCT_FROM_PRODUCER, "absent"):              (False, False),
    ("zero_gates",      ProducerStanding.DISTINCT_FROM_PRODUCER, "present_with_reason"): (True,  True),

    # NOTHING NAMES A PRODUCER: identical permissions to the row above, and that
    # identity is the property, not a coincidence. The absence of a binding adds
    # no requirement (an approving verdict still closes the task with no
    # override, which is what keeps an automatic close possible at all) and
    # grants no permission (a close with no verdict still needs the reason).
    ("all_approved",    ProducerStanding.UNLINKED, "absent"):              (True,  False),
    ("all_approved",    ProducerStanding.UNLINKED, "present_with_reason"): (True,  False),
    ("some_unapproved", ProducerStanding.UNLINKED, "absent"):              (False, False),
    ("some_unapproved", ProducerStanding.UNLINKED, "present_with_reason"): (True,  True),
    ("zero_gates",      ProducerStanding.UNLINKED, "absent"):              (False, False),
    ("zero_gates",      ProducerStanding.UNLINKED, "present_with_reason"): (True,  True),
}


def _verdict(gate_state: str):
    return derive_gate_verdict(
        [{"id": i, "status": s}
         for i, s in enumerate(_GATE_STATES[gate_state], start=1)]
    )


def test_producer_cannot_close_own_task_truth_table_is_the_complete_product():
    # The oracle for the table's honesty: not "18 cases ran" but "the declared
    # cells ARE the cartesian product". A missing cell fails here, so no cell can
    # be decided by a fallthrough that nobody wrote down.
    expected = set(
        itertools.product(_GATE_STATES, _STANDINGS, _OVERRIDE_STATES)
    )
    assert set(_TRUTH_TABLE) == expected
    assert len(_TRUTH_TABLE) == len(_GATE_STATES) * len(_STANDINGS) * len(_OVERRIDE_STATES)
    assert len(_TRUTH_TABLE) == 18


@pytest.mark.parametrize(
    "gate_state,standing,override_state,permitted,override_used",
    [
        (g, s, o, expected[0], expected[1])
        for (g, s, o), expected in sorted(
            _TRUTH_TABLE.items(), key=lambda kv: (kv[0][0], kv[0][1].value, kv[0][2])
        )
    ],
)
def test_producer_cannot_close_own_task_every_cell_of_the_truth_table_is_decided(
    gate_state, standing, override_state, permitted, override_used
):
    decision = decide_closure_under_identity(
        verdict=_verdict(gate_state),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        standing=standing,
        caller_agent=_PRODUCER,
        override_reason=_OVERRIDE_STATES[override_state],
    )
    assert decision.permitted is permitted
    assert decision.override_used is override_used
    # A record is owed exactly when the override carried the close.
    assert (decision.reason is not None) is override_used
    # A refusal always says why; a permission never does.
    assert (decision.denial_message is not None) is (not permitted)


def test_producer_cannot_close_own_task_absence_of_a_binding_grants_no_more_than_a_distinct_caller():
    # The property the unlinked branch exists to hold, asserted as an equality
    # between two whole columns rather than cell by cell: whatever an unlinked
    # caller may do, a caller distinct from a named producer may do, and no more.
    # A future edit that let the missing binding relax anything fails here even
    # if every individual cell above was updated to match it.
    for gate_state, override_state in itertools.product(_GATE_STATES, _OVERRIDE_STATES):
        unlinked = _TRUTH_TABLE[(gate_state, ProducerStanding.UNLINKED, override_state)]
        distinct = _TRUTH_TABLE[
            (gate_state, ProducerStanding.DISTINCT_FROM_PRODUCER, override_state)
        ]
        assert unlinked == distinct, (gate_state, override_state)


def test_producer_cannot_close_own_task_and_the_refusal_says_no_override_helps():
    decision = decide_closure_under_identity(
        verdict=_verdict("all_approved"),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        standing=ProducerStanding.BOUND_PRODUCER,
        caller_agent=_PRODUCER,
        override_reason=_REASON,
    )
    message = decision.denial_message
    # Who was refused, so the operator can see which identity the command ran as.
    assert _PRODUCER in message
    # That the refusal is absolute -- otherwise the reader goes to a flag that
    # cannot help them, which every other refusal in this path points at.
    assert "ABSOLUTE" in message
    assert "--override does not lift it" in message
    # And what to do instead.
    assert f"gaia task gate set-status {_BRIEF} {_ORDER}" in message


def test_producer_cannot_close_own_task_unlinked_refusal_distinguishes_why():
    # Two different missing facts demand two different corrections, so the
    # refusal has to name both rather than collapsing them into "not approved".
    unlinked = decide_closure_under_identity(
        verdict=_verdict("some_unapproved"),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        standing=ProducerStanding.UNLINKED,
        caller_agent=HUMAN_ACTOR,
    ).denial_message
    distinct = decide_closure_under_identity(
        verdict=_verdict("some_unapproved"),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        standing=ProducerStanding.DISTINCT_FROM_PRODUCER,
        caller_agent=_OTHER_AGENT,
    ).denial_message

    # The gate fact, in both.
    assert "have not passed" in unlinked and "have not passed" in distinct
    # The binding fact, only where it is true.
    assert unlinked.endswith(unlinked_denial_clause())
    assert "NO dispatch binding names who produced this task" in unlinked
    assert "NO dispatch binding names who produced this task" not in distinct
    # And the clause is explicit that the absent binding is not a permission,
    # because "nobody is named" reads as "nobody is blocked" otherwise.
    assert "not an approval" in unlinked


def test_producer_cannot_close_own_task_and_a_permitted_close_carries_no_denial_clause():
    # The clause rides a refusal, never a permission: a task that closed on its
    # verdict is not handed a warning about evidence it did not need.
    decision = decide_closure_under_identity(
        verdict=_verdict("all_approved"),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        standing=ProducerStanding.UNLINKED,
        caller_agent=HUMAN_ACTOR,
    )
    assert decision.permitted is True
    assert decision.denial_message is None


@pytest.mark.parametrize("reason", ["", "   ", "\n", 42, [], object()])
def test_producer_cannot_close_own_task_malformed_reason_uses_the_one_validator(
    reason
):
    # A second copy of the reason check is exactly what the wrapper must not
    # grow, so the rejection must come back with the channel's own message.
    with pytest.raises(ValueError) as exc:
        decide_closure_under_identity(
            verdict=_verdict("some_unapproved"),
            brief_name=_BRIEF,
            task_order_num=_ORDER,
            standing=ProducerStanding.UNLINKED,
            caller_agent=HUMAN_ACTOR,
            override_reason=reason,
        )
    assert str(exc.value) == MISSING_REASON_MESSAGE


def test_producer_cannot_close_own_task_refusal_precedes_the_argument_check():
    # Declared ordering, asserted: no argument can change a bound producer's
    # answer, so a malformed reason must not turn their absolute refusal into an
    # argument error -- that would report the wrong problem.
    decision = decide_closure_under_identity(
        verdict=_verdict("some_unapproved"),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        standing=ProducerStanding.BOUND_PRODUCER,
        caller_agent=_PRODUCER,
        override_reason="   ",
    )
    assert decision.permitted is False
    assert "ABSOLUTE" in decision.denial_message


# ---------------------------------------------------------------------------
# Standing classification and name extraction
# ---------------------------------------------------------------------------

def test_producer_cannot_close_own_task_standing_is_classified_at_name_granularity():
    names = (_PRODUCER,)
    assert classify_producer_standing(
        caller_agent=_PRODUCER, producer_agents=names
    ) is ProducerStanding.BOUND_PRODUCER
    assert classify_producer_standing(
        caller_agent=_OTHER_AGENT, producer_agents=names
    ) is ProducerStanding.DISTINCT_FROM_PRODUCER
    assert classify_producer_standing(
        caller_agent=_PRODUCER, producer_agents=()
    ) is ProducerStanding.UNLINKED
    # A human CLI caller is a known identity, not a missing one, and is not a
    # producer.
    assert classify_producer_standing(
        caller_agent=HUMAN_ACTOR, producer_agents=names
    ) is ProducerStanding.DISTINCT_FROM_PRODUCER
    # An unusable caller value never yields the standing that grants the most.
    for junk in (None, 42, [], object()):
        assert classify_producer_standing(
            caller_agent=junk, producer_agents=names
        ) is ProducerStanding.DISTINCT_FROM_PRODUCER


def test_producer_cannot_close_own_task_only_name_shaped_actors_count_as_producers():
    # A finalized row stamps the minted a+hex id, an identity space no CLI
    # invocation can produce. Keeping it would inflate the binding set with rows
    # that can never match the caller and can therefore prove nothing about them.
    rows = [
        {"agent_id": _PRODUCER},
        {"agent_id": "a" + "0123456789abcdef"},
        {"agent_id": "  "},
        {"agent_id": None},
        {"agent_id": _PRODUCER},
        "not a mapping",
    ]
    assert producer_agent_names(rows) == (_PRODUCER,)
    # An unusable collection yields no producers -- which is UNLINKED, the
    # standing that grants nothing, never a match that would refuse everyone.
    for junk in (None, "rows", {"agent_id": _PRODUCER}, 42):
        assert producer_agent_names(junk) == ()


# ---------------------------------------------------------------------------
# The writer: where the guard actually holds, against a real substrate
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


def _seed(tmp_db: Path, gate_statuses: tuple[str, ...] = (),
          bound_agent: str | None = None) -> None:
    """Seed workspace -> brief -> plan -> one pending task, gates, and binding.

    ``bound_agent`` births a real dispatch row through the writer primitive that
    the hook layer uses, rather than an INSERT written for the test: the guard
    has to read what production actually writes, including the synthetic
    contract_id shape and the agent NAME in the agent_id column.
    """
    from gaia.briefs import upsert_brief
    from gaia.store.writer import (
        add_gate_to_task,
        add_task_to_plan,
        insert_dispatched_handoff,
        upsert_plan,
    )

    upsert_brief(_WORKSPACE, _BRIEF, {"status": "open", "title": _BRIEF},
                 db_path=tmp_db)
    upsert_plan(_WORKSPACE, _BRIEF, content="plan body", status="active",
                db_path=tmp_db)
    add_task_to_plan(_WORKSPACE, _BRIEF, _ORDER, "close this task",
                     db_path=tmp_db)
    for status in gate_statuses:
        add_gate_to_task(_WORKSPACE, _BRIEF, _ORDER, "command",
                         evidence_shape="pytest -q", status=status,
                         db_path=tmp_db)
    if bound_agent is not None:
        insert_dispatched_handoff(
            contract_id=f"dispatch.session-x.{bound_agent}.{_task_row_id(tmp_db)}",
            agent_id=bound_agent,
            workspace=_WORKSPACE,
            plan_task_id=_task_row_id(tmp_db),
            kind="task_execution",
            session_id="session-x",
            db_path=tmp_db,
        )


def _task_row_id(tmp_db: Path) -> int:
    con = sqlite3.connect(str(tmp_db))
    try:
        row = con.execute(
            "SELECT t.id FROM tasks t JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id WHERE b.name = ? AND t.order_num = ?",
            (_BRIEF, _ORDER),
        ).fetchone()
        assert row is not None, "task not seeded"
        return row[0]
    finally:
        con.close()


def _task_status(tmp_db: Path) -> str:
    con = sqlite3.connect(str(tmp_db))
    try:
        row = con.execute(
            "SELECT t.status FROM tasks t JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id WHERE b.name = ? AND t.order_num = ?",
            (_BRIEF, _ORDER),
        ).fetchone()
        return row[0]
    finally:
        con.close()


def _override_rows(tmp_db: Path) -> list[tuple]:
    con = sqlite3.connect(str(tmp_db))
    try:
        return con.execute(
            "SELECT agent, result FROM harness_events WHERE type = ? ORDER BY id",
            (TASK_CLOSE_OVERRIDE_EVENT,),
        ).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("gate_statuses", list(_GATE_STATES.values()))
@pytest.mark.parametrize("override", [None, _REASON])
def test_producer_cannot_close_own_task_in_the_writer_with_or_without_override(
    tmp_db, monkeypatch, gate_statuses, override
):
    # The absolute refusal against a real substrate: a real born-at-dispatch row
    # binding `developer` to this task, and a real GAIA_DISPATCH_AGENT naming
    # them. Refused across every gate state, override or not.
    _seed(tmp_db, gate_statuses, bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _PRODUCER)
    from gaia.store.writer import set_task_status

    with pytest.raises(TaskClosureBlocked) as exc:
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, "done",
                        override_reason=override, db_path=tmp_db)

    assert "ABSOLUTE" in str(exc.value)
    # A refusal leaves nothing behind: no status change and no audit record.
    assert _task_status(tmp_db) == "pending"
    assert _override_rows(tmp_db) == []


def test_producer_cannot_close_own_task_but_a_different_agent_may_on_the_evidence(
    tmp_db, monkeypatch
):
    # The refusal is about WHO, not about the task: the same fully-approved task
    # closes for anyone else, with no override.
    _seed(tmp_db, _GATE_STATES["all_approved"], bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _OTHER_AGENT)
    from gaia.store.writer import set_task_status

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, "done", db_path=tmp_db)
    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "done"
    # Closed on the evidence, so no override was consumed and nothing is owed.
    assert _override_rows(tmp_db) == []


def test_producer_cannot_close_own_task_unlinked_close_is_refused_without_override(
    tmp_db
):
    # No binding row at all. The close is NOT free -- the absence of evidence
    # about who produced the task never becomes an approval -- and the refusal
    # names both missing facts.
    _seed(tmp_db, _GATE_STATES["some_unapproved"], bound_agent=None)
    from gaia.store.writer import set_task_status

    with pytest.raises(TaskClosureBlocked) as exc:
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, "done", db_path=tmp_db)

    message = str(exc.value)
    assert "have not passed" in message
    assert "NO dispatch binding names who produced this task" in message
    assert _task_status(tmp_db) == "pending"
    assert _override_rows(tmp_db) == []


def test_producer_cannot_close_own_task_unlinked_close_proceeds_with_the_same_override(
    tmp_db
):
    # The reused exit: the same flags, the same validator, the same channel. If
    # the unlinked branch had grown its own override path, this record would not
    # be here or would not look like the one every other override leaves.
    _seed(tmp_db, _GATE_STATES["some_unapproved"], bound_agent=None)
    from gaia.store.writer import set_task_status

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, "done",
                          override_reason=_REASON, db_path=tmp_db)
    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "done"

    rows = _override_rows(tmp_db)
    assert len(rows) == 1
    agent, result = rows[0]
    assert agent == HUMAN_ACTOR
    assert _REASON in result


def test_producer_cannot_close_own_task_unlinked_approving_verdict_needs_no_override(
    tmp_db
):
    # The exemption that keeps an automatic close possible at all: an approving
    # verdict IS the proof of verification, so it closes the task with no
    # binding and no reason to state. Reading the unlinked branch literally --
    # demanding an override here -- would block the derived close in practice,
    # since a binding is rare.
    _seed(tmp_db, _GATE_STATES["all_approved"], bound_agent=None)
    from gaia.store.writer import set_task_status

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, "done", db_path=tmp_db)
    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "done"
    assert _override_rows(tmp_db) == []


@pytest.mark.parametrize("exempt_status", ["skipped"])
def test_producer_cannot_close_own_task_yet_skipped_stays_exempt_for_them_too(
    tmp_db, monkeypatch, exempt_status
):
    # Marking a task skipped is a statement about whether the work should happen,
    # not a claim that it was verified, so the producer is not certifying
    # anything and the guard does not apply.
    _seed(tmp_db, _GATE_STATES["some_unapproved"], bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _PRODUCER)
    from gaia.store.writer import set_task_status

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, exempt_status,
                          db_path=tmp_db)
    assert res["action"] == "updated"
    assert _task_status(tmp_db) == exempt_status


def test_producer_cannot_close_own_task_yet_reopening_it_stays_exempt(
    tmp_db, monkeypatch
):
    # Reopening withdraws a closure rather than asserting one, so it carries no
    # identity condition either -- including for the producer.
    _seed(tmp_db, _GATE_STATES["all_approved"], bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _OTHER_AGENT)
    from gaia.store.writer import set_task_status
    set_task_status(_WORKSPACE, _BRIEF, _ORDER, "done", db_path=tmp_db)

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _PRODUCER)
    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, "pending", db_path=tmp_db)
    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "pending"
