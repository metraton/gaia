"""Derived task closure: a recorded gate verdict carrying the task with it.

Matchable as one suite::

    pytest tests/ -k derived_task_closure -q

Every test here carries that phrase, following the naming convention of
tests/test_task_close_condition.py and tests/test_producer_identity_close_guard.py.

Two surfaces, one semantics:

  * ``gaia.state.task_closure_derivation`` -- the pure decision (no DB, no env,
    no I/O), exercised as an EXHAUSTIVE truth table over its three inputs.
  * ``gaia.store.writer.set_gate_status`` -- the seam where a verdict is
    persisted, against a real disposable sqlite substrate, where the binding
    rows, ``GAIA_DISPATCH_AGENT`` and the task row are real rather than
    simulated. This is the surface an operator and a verifier actually reach,
    and the CLI handler is driven too so the wiring is proven end to end rather
    than one layer short of it.

The truth table is exhaustive BY CONSTRUCTION rather than by counting: the
declared table's keys are asserted equal to the full cartesian product of the
three axes, so a cell nobody wrote fails here instead of falling through to
whatever the language does next.

What is asserted goes past the enumerated branches, because the properties are
stronger than the cases:

  * AN ABSENT BINDING DOES NOT BLOCK THE AUTOMATISM. An approving verdict is
    itself the proof of verification, so a derived close needs no override and no
    record of who produced the task -- asserted both as a column equality in the
    table (unlinked grants exactly what a distinct caller grants) and against the
    substrate, by closing a task that has no binding at all and reading back that
    no override event was written.
  * THE PRODUCER NEVER CLOSES ITS OWN TASK BY AUTOMATISM. Withheld in the table
    for every gate state, and at the seam with a real binding row and a real
    dispatch identity. And withheld is not a dead end: the same evidence closes
    the task the moment the verdict is recorded from an independent identity.
  * THE PRODUCER IS STILL ALLOWED TO UN-CLOSE. The reopen is not withheld from
    anyone, because withdrawing a closure asserts nothing.
  * ONE WRITER, NO PRIVILEGED PATH. The transition goes through
    ``set_task_status`` -- observed by wrapping it -- with no override argument,
    and the production tree still holds exactly one statement that writes
    ``tasks.status``.
  * A FAILED DERIVATION CANNOT CORRUPT THE RECORDED VERDICT, AND THAT HOLDS AT
    EVERY STEP OF IT -- not only at the write. Each collaborator the derivation
    reaches for is forced to raise in turn, and in every case the gate row is
    still exactly what was asked for, the call still succeeds, and the failure is
    reported rather than swallowed. The property is the position, not the
    statement: all of this runs after the gate verdict has committed, so nothing
    in it may propagate, however pure the step is today.
  * IDEMPOTENCE. Re-recording a verdict on a task already in the implied state
    changes nothing and raises nothing, however many times it is repeated.
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from gaia.state.task_closure import derive_gate_verdict  # noqa: E402
from gaia.state.task_closure_condition import CLOSING_STATUS  # noqa: E402
from gaia.state.task_closure_derivation import (  # noqa: E402
    OPEN_STATUS,
    SET_ASIDE_STATUS,
    DerivedClosureAction,
    decide_derived_closure,
)
from gaia.state.task_closure_event import TASK_CLOSE_OVERRIDE_EVENT  # noqa: E402
from gaia.state.task_closure_identity import ProducerStanding  # noqa: E402

_WORKSPACE = "me"
_BRIEF = "derived-task-closure-brief"
_ORDER = 1
_PRODUCER = "developer"
_INDEPENDENT = "gaia-verifier"


def _gate(status: str, gate_id: int = 1) -> dict:
    """A gate mapping in the shape ``list_task_gates`` returns."""
    return {
        "id": gate_id,
        "task_id": 1,
        "verification_type": "command",
        "evidence_type": None,
        "evidence_shape": "pytest -q",
        "artifact_path": None,
        "status": status,
    }


_APPROVING = derive_gate_verdict([_gate("pass", 1), _gate("pass", 2)])
_NOT_APPROVING = derive_gate_verdict([_gate("pass", 1), _gate("fail", 2)])
_NO_GATES = derive_gate_verdict([])


# ---------------------------------------------------------------------------
# The pure decision, as an exhaustive truth table
# ---------------------------------------------------------------------------
#
# Axes: whether the gates approve x the task's current status x the caller's
# standing relative to the task's known producers. Every cell is declared; the
# product is asserted below, so an unwritten cell is a failure rather than a
# default.

_CLOSE = DerivedClosureAction.CLOSE
_REOPEN = DerivedClosureAction.REOPEN
_NONE = DerivedClosureAction.NONE

_BOUND = ProducerStanding.BOUND_PRODUCER
_DISTINCT = ProducerStanding.DISTINCT_FROM_PRODUCER
_UNLINKED = ProducerStanding.UNLINKED

_TRUTH_TABLE: dict[tuple[bool, str, ProducerStanding], DerivedClosureAction] = {
    # Approving verdict, task still open: the one cell family that closes -- and
    # the producer's cell in it is the one that does not.
    (True, OPEN_STATUS, _BOUND): _NONE,
    (True, OPEN_STATUS, _DISTINCT): _CLOSE,
    (True, OPEN_STATUS, _UNLINKED): _CLOSE,
    # Approving verdict, task already closed: idempotence, decided rather than
    # left to the write to absorb as a no-op.
    (True, CLOSING_STATUS, _BOUND): _NONE,
    (True, CLOSING_STATUS, _DISTINCT): _NONE,
    (True, CLOSING_STATUS, _UNLINKED): _NONE,
    # Approving verdict, task set aside: evidence does not resurrect a task a
    # human decided should not happen (and the lifecycle has no such edge).
    (True, SET_ASIDE_STATUS, _BOUND): _NONE,
    (True, SET_ASIDE_STATUS, _DISTINCT): _NONE,
    (True, SET_ASIDE_STATUS, _UNLINKED): _NONE,
    # No approving verdict, task open: nothing to do in either direction.
    (False, OPEN_STATUS, _BOUND): _NONE,
    (False, OPEN_STATUS, _DISTINCT): _NONE,
    (False, OPEN_STATUS, _UNLINKED): _NONE,
    # No approving verdict, task closed: the closure no longer follows, so it is
    # withdrawn -- from every caller, the producer included.
    (False, CLOSING_STATUS, _BOUND): _REOPEN,
    (False, CLOSING_STATUS, _DISTINCT): _REOPEN,
    (False, CLOSING_STATUS, _UNLINKED): _REOPEN,
    # No approving verdict, task set aside: it asserts no closure to withdraw.
    (False, SET_ASIDE_STATUS, _BOUND): _NONE,
    (False, SET_ASIDE_STATUS, _DISTINCT): _NONE,
    (False, SET_ASIDE_STATUS, _UNLINKED): _NONE,
}

_TASK_STATUSES = (OPEN_STATUS, CLOSING_STATUS, SET_ASIDE_STATUS)
_STANDINGS = (_BOUND, _DISTINCT, _UNLINKED)


def test_derived_task_closure_truth_table_is_exhaustive():
    expected_keys = set(
        itertools.product((True, False), _TASK_STATUSES, _STANDINGS)
    )

    assert set(_TRUTH_TABLE) == expected_keys
    assert len(_TRUTH_TABLE) == 18


@pytest.mark.parametrize("cell", sorted(_TRUTH_TABLE, key=str))
def test_derived_task_closure_truth_table_cell(cell):
    approving, task_status, standing = cell
    verdict = _APPROVING if approving else _NOT_APPROVING

    decision = decide_derived_closure(
        verdict=verdict, task_status=task_status, standing=standing
    )

    assert decision.action is _TRUTH_TABLE[cell]
    # Every branch says why, acting or not: the caller reports a transition the
    # operator did not ask for, and an inaction they may be waiting on.
    assert decision.why
    if decision.action is _CLOSE:
        assert decision.target_status == CLOSING_STATUS
    elif decision.action is _REOPEN:
        assert decision.target_status == OPEN_STATUS
    else:
        assert decision.target_status is None
    assert decision.acts is (decision.action is not _NONE)


def test_derived_task_closure_is_never_produced_for_a_bound_producer():
    # The property behind the cells: across the WHOLE table there is no
    # combination of inputs that closes a task for the agent it was dispatched
    # to -- not "closes it only without an override", which is what a
    # case-by-case reading would leave room for.
    closing = [
        cell for cell, action in _TRUTH_TABLE.items() if action is _CLOSE
    ]

    assert closing, "the table must close something, or it proves nothing"
    assert all(standing is not _BOUND for _, _, standing in closing)


def test_derived_task_closure_treats_an_absent_binding_exactly_as_a_third_party():
    # An absent binding is not evidence about the caller, so it must neither
    # grant nor withhold anything relative to a caller known to be someone else.
    # Asserted as a column equality so a future divergence fails here rather
    # than in whichever case happens to be written.
    for approving, task_status in itertools.product((True, False), _TASK_STATUSES):
        assert (
            _TRUTH_TABLE[(approving, task_status, _UNLINKED)]
            is _TRUTH_TABLE[(approving, task_status, _DISTINCT)]
        )

    assert _TRUTH_TABLE[(True, OPEN_STATUS, _UNLINKED)] is _CLOSE


def test_derived_task_closure_reopen_is_withheld_from_nobody():
    # Withdrawing a closure asserts nothing about verification, so the identity
    # axis must not touch it -- including for the producer, whose failing
    # verdict on its own work is exactly the reopen we want to keep.
    for standing in _STANDINGS:
        assert _TRUTH_TABLE[(False, CLOSING_STATUS, standing)] is _REOPEN


def test_derived_task_closure_never_follows_from_zero_gates():
    # A task nobody wrote a gate for has no verdict, and no verdict is not an
    # approving one: the automatism must be unreachable for it in every cell.
    assert _NO_GATES.approving is False
    for task_status, standing in itertools.product(_TASK_STATUSES, _STANDINGS):
        decision = decide_derived_closure(
            verdict=_NO_GATES, task_status=task_status, standing=standing
        )
        assert decision.action is not _CLOSE


@pytest.mark.parametrize("task_status", [None, "", "DONE", "archived", 7, object()])
def test_derived_task_closure_leaves_an_unclassifiable_task_status_alone(task_status):
    for standing in _STANDINGS:
        for verdict in (_APPROVING, _NOT_APPROVING):
            decision = decide_derived_closure(
                verdict=verdict, task_status=task_status, standing=standing
            )
            assert decision.action is _NONE
            assert decision.target_status is None


# ---------------------------------------------------------------------------
# The seam -- isolated substrate per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path`` and start from a human caller."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


def _seed(tmp_db: Path, gate_count: int = 2, bound_agent: str | None = None) -> list[int]:
    """Seed workspace -> brief -> plan -> one pending task, gates, and binding.

    ``bound_agent`` births a real dispatch row through the same writer primitive
    the hook layer uses, so the guard reads what production actually writes --
    the agent NAME in the actor column, not a shape invented for the test.
    Returns the created gate ids, in order.
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
    add_task_to_plan(_WORKSPACE, _BRIEF, _ORDER, "wire the derived closure",
                     db_path=tmp_db)

    gate_ids = [
        add_gate_to_task(_WORKSPACE, _BRIEF, _ORDER, "command",
                         evidence_shape="pytest -q", db_path=tmp_db)["gate_id"]
        for _ in range(gate_count)
    ]

    if bound_agent is not None:
        task_id = _task_row_id(tmp_db)
        insert_dispatched_handoff(
            contract_id=f"dispatch.session-x.{bound_agent}.{task_id}",
            agent_id=bound_agent,
            workspace=_WORKSPACE,
            plan_task_id=task_id,
            kind="task_execution",
            session_id="session-x",
            db_path=tmp_db,
        )
    return gate_ids


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


def _gate_statuses(tmp_db: Path) -> list[str]:
    con = sqlite3.connect(str(tmp_db))
    try:
        return [
            r[0] for r in con.execute(
                "SELECT status FROM task_gates WHERE task_id = ? ORDER BY id",
                (_task_row_id(tmp_db),),
            ).fetchall()
        ]
    finally:
        con.close()


def _override_event_count(tmp_db: Path) -> int:
    con = sqlite3.connect(str(tmp_db))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM harness_events WHERE type = ?",
            (TASK_CLOSE_OVERRIDE_EVENT,),
        ).fetchone()[0]
    finally:
        con.close()


def _pass(tmp_db: Path, gate_id: int, status: str = "pass") -> dict:
    from gaia.store.writer import set_gate_status
    return set_gate_status(_WORKSPACE, _BRIEF, _ORDER, gate_id, status,
                           db_path=tmp_db)


def _derived(result: dict) -> dict:
    from gaia.store.writer import DERIVED_CLOSURE_RESULT_KEY
    assert DERIVED_CLOSURE_RESULT_KEY in result, (
        "the seam must always report what the verdict implied, so 'ran and did "
        "nothing' is distinguishable from 'did not run'"
    )
    return result[DERIVED_CLOSURE_RESULT_KEY]


# ---------------------------------------------------------------------------
# Branch 1: an approving verdict closes the task, with no manual step
# ---------------------------------------------------------------------------

def test_derived_task_closure_closes_the_task_when_the_last_gate_passes(tmp_db):
    first, second = _seed(tmp_db)

    partial = _pass(tmp_db, first)
    assert _task_status(tmp_db) == OPEN_STATUS
    assert _derived(partial)["action"] == "none"

    final = _pass(tmp_db, second)

    assert _task_status(tmp_db) == CLOSING_STATUS
    derived = _derived(final)
    assert derived["action"] == "close"
    assert derived["old_status"] == OPEN_STATUS
    assert derived["new_status"] == CLOSING_STATUS
    assert derived["task_action"] == "updated"
    # The verdict is what closed it, and it is still exactly as recorded.
    assert _gate_statuses(tmp_db) == ["pass", "pass"]


def test_derived_task_closure_needs_no_binding_and_writes_no_override(tmp_db):
    # The exemption that makes the automatism possible at all: nothing on record
    # names who produced this task, and the close still happens -- with no
    # override, because an unattended close has no reason to give and needs none.
    gate, = _seed(tmp_db, gate_count=1, bound_agent=None)

    result = _pass(tmp_db, gate)

    assert _task_status(tmp_db) == CLOSING_STATUS
    assert _derived(result)["action"] == "close"
    assert _override_event_count(tmp_db) == 0


def test_derived_task_closure_closes_a_bound_task_for_an_independent_caller(
    tmp_db, monkeypatch
):
    gate, = _seed(tmp_db, gate_count=1, bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _INDEPENDENT)

    result = _pass(tmp_db, gate)

    assert _task_status(tmp_db) == CLOSING_STATUS
    assert _derived(result)["action"] == "close"
    assert _override_event_count(tmp_db) == 0


# ---------------------------------------------------------------------------
# Branch 2: a partial or failing verdict does not close
# ---------------------------------------------------------------------------

def test_derived_task_closure_does_not_close_on_a_partial_verdict(tmp_db):
    first, _second = _seed(tmp_db)

    result = _pass(tmp_db, first)

    assert _task_status(tmp_db) == OPEN_STATUS
    assert _derived(result)["action"] == "none"
    assert _gate_statuses(tmp_db) == ["pass", "pending"]


def test_derived_task_closure_does_not_close_when_a_gate_fails(tmp_db):
    first, second = _seed(tmp_db)

    _pass(tmp_db, first, "pass")
    result = _pass(tmp_db, second, "fail")

    assert _task_status(tmp_db) == OPEN_STATUS
    assert _derived(result)["action"] == "none"


# ---------------------------------------------------------------------------
# Branch 3: a failing re-verdict on a closed task returns it to pending
# ---------------------------------------------------------------------------

def test_derived_task_closure_reopens_a_closed_task_on_a_failing_reverdict(tmp_db):
    first, second = _seed(tmp_db)
    _pass(tmp_db, first)
    _pass(tmp_db, second)
    assert _task_status(tmp_db) == CLOSING_STATUS

    result = _pass(tmp_db, second, "fail")

    assert _task_status(tmp_db) == OPEN_STATUS
    derived = _derived(result)
    assert derived["action"] == "reopen"
    assert derived["old_status"] == CLOSING_STATUS
    assert derived["new_status"] == OPEN_STATUS
    assert _gate_statuses(tmp_db) == ["pass", "fail"]


def test_derived_task_closure_reopens_when_a_verdict_is_withdrawn_to_pending(tmp_db):
    # Not only an explicit 'fail': any verdict the gates no longer support
    # withdraws the closure, which is the fail-closed direction and matches what
    # the closure condition itself asks of a manual close.
    gate, = _seed(tmp_db, gate_count=1)
    _pass(tmp_db, gate)
    assert _task_status(tmp_db) == CLOSING_STATUS

    result = _pass(tmp_db, gate, "pending")

    assert _task_status(tmp_db) == OPEN_STATUS
    assert _derived(result)["action"] == "reopen"


# ---------------------------------------------------------------------------
# The producer never closes its own task by automatism
# ---------------------------------------------------------------------------

def test_derived_task_closure_is_withheld_from_the_bound_producer(
    tmp_db, monkeypatch
):
    gate, = _seed(tmp_db, gate_count=1, bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _PRODUCER)

    result = _pass(tmp_db, gate)

    # The verdict IS recorded -- the producer may report what it observed.
    assert _gate_statuses(tmp_db) == ["pass"]
    # What is withheld is the task closing on the strength of it.
    assert _task_status(tmp_db) == OPEN_STATUS
    derived = _derived(result)
    assert derived["action"] == "none"
    assert "dispatched to" in derived["why"]
    # And it is withheld silently in the substrate: no refusal is recorded as a
    # defect, because nothing was refused -- nobody asked to close anything.
    assert _override_event_count(tmp_db) == 0


def test_derived_task_closure_withheld_from_the_producer_is_not_a_dead_end(
    tmp_db, monkeypatch
):
    gate, = _seed(tmp_db, gate_count=1, bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _PRODUCER)
    _pass(tmp_db, gate)
    assert _task_status(tmp_db) == OPEN_STATUS

    # The same evidence, re-recorded from an independent identity, derives the
    # close: what the producer could not do was certify, not verify.
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _INDEPENDENT)
    result = _pass(tmp_db, gate)

    assert _task_status(tmp_db) == CLOSING_STATUS
    assert _derived(result)["action"] == "close"


def test_derived_task_closure_still_lets_the_producer_reopen(tmp_db, monkeypatch):
    first, second = _seed(tmp_db, bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _INDEPENDENT)
    _pass(tmp_db, first)
    _pass(tmp_db, second)
    assert _task_status(tmp_db) == CLOSING_STATUS

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _PRODUCER)
    result = _pass(tmp_db, second, "fail")

    assert _task_status(tmp_db) == OPEN_STATUS
    assert _derived(result)["action"] == "reopen"


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_derived_task_closure_is_idempotent_on_an_already_closed_task(tmp_db):
    gate, = _seed(tmp_db, gate_count=1)
    _pass(tmp_db, gate)
    assert _task_status(tmp_db) == CLOSING_STATUS

    for _ in range(3):
        result = _pass(tmp_db, gate)
        assert _task_status(tmp_db) == CLOSING_STATUS
        derived = _derived(result)
        # Decided as inaction, not absorbed as a write that changes nothing.
        assert derived["action"] == "none"
        assert "already closed" in derived["why"]

    assert _override_event_count(tmp_db) == 0
    assert _gate_statuses(tmp_db) == ["pass"]


def test_derived_task_closure_is_idempotent_on_a_repeated_failing_verdict(tmp_db):
    gate, = _seed(tmp_db, gate_count=1)
    _pass(tmp_db, gate)

    first_reopen = _pass(tmp_db, gate, "fail")
    assert _derived(first_reopen)["action"] == "reopen"

    for _ in range(3):
        result = _pass(tmp_db, gate, "fail")
        assert _task_status(tmp_db) == OPEN_STATUS
        assert _derived(result)["action"] == "none"


def test_derived_task_closure_does_not_touch_a_task_set_aside(tmp_db):
    from gaia.store.writer import set_task_status

    gate, = _seed(tmp_db, gate_count=1)
    set_task_status(_WORKSPACE, _BRIEF, _ORDER, SET_ASIDE_STATUS, db_path=tmp_db)
    assert _task_status(tmp_db) == SET_ASIDE_STATUS

    result = _pass(tmp_db, gate)

    assert _task_status(tmp_db) == SET_ASIDE_STATUS
    assert _derived(result)["action"] == "none"


# ---------------------------------------------------------------------------
# One writer, no privileged path
# ---------------------------------------------------------------------------

def test_derived_task_closure_transitions_through_the_single_writer(
    tmp_db, monkeypatch
):
    from gaia.store import writer

    calls: list[tuple] = []
    real = writer.set_task_status

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(writer, "set_task_status", _spy)

    gate, = _seed(tmp_db, gate_count=1)
    _pass(tmp_db, gate)

    assert _task_status(tmp_db) == CLOSING_STATUS
    assert len(calls) == 1, "the derived close must reach exactly one writer, once"
    args, kwargs = calls[0]
    assert args[:4] == (_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS)
    # No override travels with it: the close rests on the evidence, and passing a
    # reason would both be a lie and record a defect for a verified closure.
    assert kwargs.get("override_reason") is None


def test_derived_task_closure_keeps_one_task_status_writer_in_the_tree():
    # The structural half of the same property, and the reason the derivation
    # calls a function instead of issuing its own statement: the production tree
    # must still hold exactly one place where a task's status is written.
    needle = "UPDATE tasks SET status"
    hits = [
        f"{path.relative_to(_REPO_ROOT)}"
        for root in ("gaia", "hooks", "bin")
        for path in sorted((_REPO_ROOT / root).rglob("*.py"))
        if needle in path.read_text(encoding="utf-8")
    ]

    assert hits == ["gaia/store/writer.py"], hits


# ---------------------------------------------------------------------------
# A failed derivation cannot corrupt the recorded verdict
# ---------------------------------------------------------------------------

def test_derived_task_closure_failure_leaves_the_recorded_verdict_intact(
    tmp_db, monkeypatch
):
    from gaia.store import writer

    def _explode(*args, **kwargs):
        raise RuntimeError("the transition blew up")

    gate, = _seed(tmp_db, gate_count=1)
    monkeypatch.setattr(writer, "set_task_status", _explode)

    result = _pass(tmp_db, gate)

    # The verdict the caller asked to record survived, and the call still
    # succeeded: the gate write committed before the derivation was consulted.
    assert _gate_statuses(tmp_db) == ["pass"]
    assert result["new_status"] == "pass"
    assert _task_status(tmp_db) == OPEN_STATUS
    # And the failure is reported, not swallowed.
    derived = _derived(result)
    assert derived["action"] == "error"
    assert derived["intended_action"] == "close"
    assert "the transition blew up" in derived["error"]


def test_derived_task_closure_reports_a_refused_transition_without_raising(
    tmp_db, monkeypatch
):
    # A refusal from the shared guard is a failure like any other at this seam:
    # the recorded verdict must outlive it. Simulated by refusing the way the
    # guard does, so the seam is exercised against the real exception type.
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.store import writer

    def _refuse(*args, **kwargs):
        raise TaskClosureBlocked("refused by the closure guard")

    gate, = _seed(tmp_db, gate_count=1)
    monkeypatch.setattr(writer, "set_task_status", _refuse)

    result = _pass(tmp_db, gate)

    assert _gate_statuses(tmp_db) == ["pass"]
    assert _derived(result)["action"] == "error"
    assert "refused by the closure guard" in _derived(result)["error"]


# ---------------------------------------------------------------------------
# ...and that holds at EVERY step of the derivation, not only at the write
# ---------------------------------------------------------------------------
#
# The guarantee is about the position, not about one statement: everything the
# derivation does runs after the gate verdict has committed, so none of it may
# propagate -- including the steps that are pure today and could stop being pure
# tomorrow, and including the deferred imports, which fail without any impurity
# at all. A guard narrower than the promise is a promise a reader would trust
# and not receive.

_DERIVATION_COLLABORATORS = [
    ("gaia.state.task_closure_event", "resolve_actor"),
    ("gaia.state.task_closure", "derive_gate_verdict"),
    ("gaia.state.task_closure_identity", "producer_agent_names"),
    ("gaia.state.task_closure_identity", "classify_producer_standing"),
    ("gaia.state.task_closure_derivation", "decide_derived_closure"),
    ("gaia.store.writer", "set_task_status"),
]

_ERROR_OUTCOME_KEYS = {
    "action", "why", "gate_count", "verdict_approving", "error", "intended_action",
}


def _break(monkeypatch, module_name: str, attr: str, message: str) -> None:
    """Force one collaborator of the derivation to raise.

    Patched on the DEFINING module rather than on the writer, because the writer
    imports every one of these inside the function -- which is what makes the
    imports themselves part of what the guard must cover.
    """
    module = importlib.import_module(module_name)

    def _explode(*args, **kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(module, attr, _explode)


@pytest.mark.parametrize(
    "module_name,attr", _DERIVATION_COLLABORATORS,
    ids=[f"{m.rsplit('.', 1)[-1]}.{a}" for m, a in _DERIVATION_COLLABORATORS],
)
def test_derived_task_closure_no_step_of_the_derivation_escapes(
    tmp_db, monkeypatch, module_name, attr
):
    gate, = _seed(tmp_db, gate_count=1)
    _break(monkeypatch, module_name, attr, f"{attr} blew up")

    # No pytest.raises: the assertion IS that this returns. A propagating
    # exception fails the test as an error at exactly this line.
    result = _pass(tmp_db, gate)

    # The verdict the caller asked to record is committed and untouched.
    assert _gate_statuses(tmp_db) == ["pass"]
    assert result["status"] == "applied"
    assert result["new_status"] == "pass"
    # The task did not move -- the automatism failed, it did not half-happen.
    assert _task_status(tmp_db) == OPEN_STATUS
    # And the failure is reported in the result, in the shape the success path
    # returns, so a caller reads it the same way wherever it broke.
    derived = _derived(result)
    assert derived["action"] == "error"
    assert f"RuntimeError: {attr} blew up" == derived["error"]
    assert set(derived) >= _ERROR_OUTCOME_KEYS
    assert derived["intended_action"]


def test_derived_task_closure_failure_in_the_pure_decision_does_not_escape(
    tmp_db, monkeypatch
):
    # The named case behind the sweep above, kept explicit because it is the one
    # a narrower guard actually let through: the decision is the pure half, it
    # documents "never raises", and the guard used to start after it.
    gate, = _seed(tmp_db, gate_count=1)
    _break(
        monkeypatch, "gaia.state.task_closure_derivation",
        "decide_derived_closure", "the pure decision blew up",
    )

    result = _pass(tmp_db, gate)

    assert _gate_statuses(tmp_db) == ["pass"]
    assert _task_status(tmp_db) == OPEN_STATUS
    derived = _derived(result)
    assert derived["action"] == "error"
    assert "the pure decision blew up" in derived["error"]
    # No decision was reached, so none is claimed as the intent.
    assert derived["intended_action"] == "derivation"


def test_derived_task_closure_failure_in_identity_resolution_does_not_escape(
    tmp_db, monkeypatch
):
    # Identity is resolved from the environment and from the binding rows, ahead
    # of the decision -- the earliest step, and therefore the one furthest from
    # a guard placed around the write.
    gate, = _seed(tmp_db, gate_count=1, bound_agent=_PRODUCER)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", _INDEPENDENT)
    _break(
        monkeypatch, "gaia.state.task_closure_event",
        "resolve_actor", "identity resolution blew up",
    )

    result = _pass(tmp_db, gate)

    assert _gate_statuses(tmp_db) == ["pass"]
    assert _task_status(tmp_db) == OPEN_STATUS
    derived = _derived(result)
    assert derived["action"] == "error"
    assert "identity resolution blew up" in derived["error"]


def test_derived_task_closure_survives_an_unimportable_derivation_module(
    tmp_db, monkeypatch
):
    # The trigger that needs no impurity anywhere: the imports are deferred, so
    # they run after the commit like everything else and can fail on their own.
    gate, = _seed(tmp_db, gate_count=1)
    monkeypatch.setitem(sys.modules, "gaia.state.task_closure_derivation", None)

    result = _pass(tmp_db, gate)

    assert _gate_statuses(tmp_db) == ["pass"]
    assert _task_status(tmp_db) == OPEN_STATUS
    derived = _derived(result)
    assert derived["action"] == "error"
    # ModuleNotFoundError is an ImportError; what matters is that the failing
    # import is named in the report instead of ending the call.
    assert derived["error"].startswith(("ImportError:", "ModuleNotFoundError:"))
    assert "gaia.state.task_closure_derivation" in derived["error"]


def test_derived_task_closure_cli_reports_a_failed_decision_and_still_exits_zero(
    tmp_db, tmp_path, monkeypatch, capsys
):
    # The operator-facing half of the same property: a derivation that broke
    # before deciding anything is still a recorded verdict, announced as a
    # warning and not as a failed command.
    monkeypatch.chdir(tmp_path)
    gate, = _seed(tmp_db, gate_count=1)
    _break(
        monkeypatch, "gaia.state.task_closure_derivation",
        "decide_derived_closure", "the pure decision blew up",
    )

    rc = _cli_set_gate_status(gate, "pass", as_json=False)
    captured = capsys.readouterr()

    assert rc == 0
    assert _gate_statuses(tmp_db) == ["pass"]
    assert "WARNING" in captured.err
    assert "the pure decision blew up" in captured.err


# ---------------------------------------------------------------------------
# The CLI, so the wiring is proven at the door an operator actually uses
# ---------------------------------------------------------------------------

def _cli_set_gate_status(gate_id: int, status: str, as_json: bool):
    from cli.task import _cmd_gate_set_status

    return _cmd_gate_set_status(argparse.Namespace(
        brief=_BRIEF, order_num=_ORDER, gate_id=gate_id, status=status,
        workspace=_WORKSPACE, json=as_json,
    ))


def test_derived_task_closure_reaches_the_task_through_the_cli(
    tmp_db, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    gate, = _seed(tmp_db, gate_count=1)

    rc = _cli_set_gate_status(gate, "pass", as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["derived_closure"]["action"] == "close"
    assert _task_status(tmp_db) == CLOSING_STATUS


def test_derived_task_closure_is_announced_in_the_cli_text_output(
    tmp_db, tmp_path, monkeypatch, capsys
):
    # A status change the operator did not type must not be silent, or the
    # substrate looks like it moved on its own.
    monkeypatch.chdir(tmp_path)
    gate, = _seed(tmp_db, gate_count=1)

    rc = _cli_set_gate_status(gate, "pass", as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Derived close" in out
    assert CLOSING_STATUS in out


def test_derived_task_closure_cli_reports_a_failed_derivation_and_still_exits_zero(
    tmp_db, tmp_path, monkeypatch, capsys
):
    from gaia.store import writer

    def _explode(*args, **kwargs):
        raise RuntimeError("the transition blew up")

    monkeypatch.chdir(tmp_path)
    gate, = _seed(tmp_db, gate_count=1)
    monkeypatch.setattr(writer, "set_task_status", _explode)

    rc = _cli_set_gate_status(gate, "pass", as_json=False)
    captured = capsys.readouterr()

    # Zero, because the verdict the operator asked for IS recorded -- a non-zero
    # exit would invite them to re-issue a write that already landed.
    assert rc == 0
    assert _gate_statuses(tmp_db) == ["pass"]
    assert "WARNING" in captured.err
    assert "the transition blew up" in captured.err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
