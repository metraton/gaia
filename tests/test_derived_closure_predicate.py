"""Derivation of a task's closure verdict from its persisted gates.

Matchable by ``pytest tests/ -k derived_closure_predicate -q``.

Two surfaces under test, one semantics:

  * ``gaia.state.task_closure.derive_gate_verdict`` -- the pure predicate over
    gate mappings (no DB, no env, no I/O).
  * ``gaia.store.writer.read_task_gate_verdict`` -- the read seam that fetches
    a task's persisted gates and delegates to it, exercised against a real,
    disposable sqlite substrate (``GAIA_DATA_DIR`` -> ``tmp_path``, the
    established convention in tests/cli/test_gate_status_write.py).

What is asserted goes past the four status branches, because the properties the
primitive has to hold are stronger than the enumerated cases:

  * fail closed on an EMPTY gate set -- the case a vacuous ``all()`` would
    silently report as approved, i.e. approval derived from absent evidence;
  * fail closed on a status outside the vocabulary and on a malformed input,
    neither coerced toward 'pass';
  * NO WRITE from either surface -- proven by observing the substrate before
    and after, and by making every writer entry point explode if reached;
  * IDEMPOTENCE -- repeated calls yield equal verdicts and mutate neither the
    input mappings nor the substrate;
  * INDEPENDENCE from any dispatch coordinate -- the verdict is identical
    whatever ``GAIA_DISPATCH_AGENT`` says, which is what lets the seam that
    invokes it be swapped without changing the answer.
"""

from __future__ import annotations

import copy
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state import VALID_GATE_STATUSES  # noqa: E402
from gaia.state.task_closure import (  # noqa: E402
    APPROVING_GATE_STATUS,
    EMPTY_GATE_SET_REASON,
    MALFORMED_GATE_KEY,
    OFF_VOCABULARY_GATE_KEY,
    derive_gate_verdict,
)

_BRIEF = "derived-closure-brief"


def _gate(status, gate_id: int = 1, vtype: str = "command") -> dict:
    """A gate mapping in the shape list_task_gates returns."""
    return {
        "id": gate_id,
        "task_id": 1,
        "verification_type": vtype,
        "evidence_type": None,
        "evidence_shape": "pytest -q",
        "artifact_path": None,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Pure predicate: the four status branches
# ---------------------------------------------------------------------------

def test_all_gates_pass_is_an_approving_verdict():
    gates = [
        _gate("pass", gate_id=1, vtype="command"),
        _gate("pass", gate_id=2, vtype="semantic"),
        _gate("pass", gate_id=3, vtype="self_review"),
    ]

    verdict = derive_gate_verdict(gates)

    assert verdict.approving is True
    assert verdict.gate_count == 3
    assert verdict.status_counts == {"pass": 3}
    assert verdict.reasons == []


def test_single_passing_gate_is_an_approving_verdict():
    verdict = derive_gate_verdict([_gate("pass")])

    assert verdict.approving is True
    assert verdict.gate_count == 1
    assert verdict.reasons == []


def test_any_pending_gate_is_not_approving():
    gates = [_gate("pass", gate_id=1), _gate("pending", gate_id=2)]

    verdict = derive_gate_verdict(gates)

    assert verdict.approving is False
    assert verdict.gate_count == 2
    assert verdict.status_counts == {"pass": 1, "pending": 1}
    # The reason has to name what is outstanding and in which state, so a
    # caller refusing to close can say which gate still needs a verdict.
    assert any("pending=1" in reason for reason in verdict.reasons)


def test_any_failing_gate_is_not_approving():
    gates = [_gate("pass", gate_id=1), _gate("fail", gate_id=2)]

    verdict = derive_gate_verdict(gates)

    assert verdict.approving is False
    assert verdict.status_counts == {"pass": 1, "fail": 1}
    assert any("fail=1" in reason for reason in verdict.reasons)


def test_all_gates_failing_is_not_approving():
    verdict = derive_gate_verdict(
        [_gate("fail", gate_id=1), _gate("fail", gate_id=2)]
    )

    assert verdict.approving is False
    assert verdict.status_counts == {"fail": 2}


def test_mixed_pending_and_failing_is_not_approving_and_reports_both():
    gates = [
        _gate("pending", gate_id=1),
        _gate("fail", gate_id=2),
        _gate("pass", gate_id=3),
    ]

    verdict = derive_gate_verdict(gates)

    assert verdict.approving is False
    outstanding = " ".join(verdict.reasons)
    assert "pending=1" in outstanding
    assert "fail=1" in outstanding


# ---------------------------------------------------------------------------
# Pure predicate: ZERO gates is never a pass by emptiness
# ---------------------------------------------------------------------------

def test_zero_gates_is_not_an_approving_verdict():
    verdict = derive_gate_verdict([])

    assert verdict.approving is False
    assert verdict.gate_count == 0
    assert verdict.status_counts == {}
    # Reported as its own, distinguishable reason: "no gates declared" and
    # "gates outstanding" call for different corrections.
    assert verdict.reasons == [EMPTY_GATE_SET_REASON]


def test_zero_gates_does_not_inherit_the_vacuous_all_result():
    # all([]) is True; the predicate must NOT agree with it. This is the
    # regression that would turn absence of evidence into approval.
    assert all(g["status"] == APPROVING_GATE_STATUS for g in []) is True
    assert derive_gate_verdict([]).approving is False


def test_empty_tuple_is_also_not_approving():
    verdict = derive_gate_verdict(())

    assert verdict.approving is False
    assert verdict.reasons == [EMPTY_GATE_SET_REASON]


# ---------------------------------------------------------------------------
# Pure predicate: fail closed on anything that is not exactly 'pass'
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status",
    [
        "PASS",
        "Pass",
        "passed",
        "approved",
        "skipped",
        "done",
        "",
        "   ",
        None,
        1,
        True,
        ["pass"],
    ],
)
def test_off_vocabulary_status_is_not_approving(status):
    verdict = derive_gate_verdict([_gate(status)])

    assert verdict.approving is False
    assert verdict.status_counts == {OFF_VOCABULARY_GATE_KEY: 1}
    assert any("outside" in reason for reason in verdict.reasons)


def test_missing_status_key_is_not_approving():
    gate = _gate("pass")
    del gate["status"]

    verdict = derive_gate_verdict([gate])

    assert verdict.approving is False
    assert verdict.status_counts == {OFF_VOCABULARY_GATE_KEY: 1}


def test_a_passing_gate_does_not_rescue_an_off_vocabulary_sibling():
    gates = [_gate("pass", gate_id=1), _gate("PASS", gate_id=2)]

    verdict = derive_gate_verdict(gates)

    assert verdict.approving is False
    assert verdict.status_counts == {"pass": 1, OFF_VOCABULARY_GATE_KEY: 1}


def test_off_vocabulary_reason_names_the_gate_by_persisted_id():
    verdict = derive_gate_verdict([_gate("bogus", gate_id=77)])

    assert verdict.approving is False
    assert any("id=77" in reason for reason in verdict.reasons)


@pytest.mark.parametrize("element", [None, "pass", 42, ["pass"]])
def test_malformed_gate_element_is_not_approving(element):
    verdict = derive_gate_verdict([element])

    assert verdict.approving is False
    assert verdict.status_counts == {MALFORMED_GATE_KEY: 1}
    assert any("not a mapping" in reason for reason in verdict.reasons)


@pytest.mark.parametrize(
    "collection",
    [
        None,
        "pass",
        b"pass",
        42,
        {"status": "pass"},
        (g for g in [{"status": "pass"}]),
    ],
)
def test_non_sequence_input_is_not_approving(collection):
    verdict = derive_gate_verdict(collection)

    assert verdict.approving is False
    assert verdict.gate_count == 0
    assert any("sequence of gate mappings" in reason for reason in verdict.reasons)


def test_vocabulary_is_read_from_the_single_source_of_truth():
    # The predicate must not carry its own copy of the status vocabulary:
    # a value the DB CHECK accepts and the predicate does not (or vice versa)
    # is exactly how a fail-closed rule drifts open.
    from gaia.state import task_closure

    assert task_closure.VALID_GATE_STATUSES == tuple(VALID_GATE_STATUSES)
    assert APPROVING_GATE_STATUS in VALID_GATE_STATUSES


# ---------------------------------------------------------------------------
# Pure predicate: idempotence and non-mutation of the input
# ---------------------------------------------------------------------------

def test_repeated_derivation_yields_equal_verdicts():
    gates = [_gate("pass", gate_id=1), _gate("pending", gate_id=2)]

    first = derive_gate_verdict(gates)
    second = derive_gate_verdict(gates)
    third = derive_gate_verdict(gates)

    assert first == second == third


def test_derivation_does_not_mutate_the_gates_it_reads():
    gates = [
        _gate("pass", gate_id=1),
        _gate("pending", gate_id=2),
        _gate("bogus", gate_id=3),
    ]
    before = copy.deepcopy(gates)

    derive_gate_verdict(gates)
    derive_gate_verdict(gates)

    assert gates == before


# ---------------------------------------------------------------------------
# DB read seam -- isolated substrate per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_task(tmp_db: Path, brief: str = _BRIEF, order_num: int = 1) -> None:
    """Seed workspace 'me' -> brief -> plan -> one pending task."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan, add_task_to_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, order_num, "derive this task's verdict",
                     db_path=tmp_db)


def _snapshot(db_path: Path) -> dict:
    """Row-level snapshot of every table the derivation could conceivably touch."""
    con = sqlite3.connect(str(db_path))
    try:
        return {
            "tasks": con.execute(
                "SELECT id, plan_id, order_num, status FROM tasks ORDER BY id"
            ).fetchall(),
            "task_gates": con.execute(
                "SELECT id, task_id, verification_type, status FROM task_gates "
                "ORDER BY id"
            ).fetchall(),
            "harness_events": con.execute(
                "SELECT COUNT(*) FROM harness_events"
            ).fetchone(),
        }
    finally:
        con.close()


def test_read_seam_returns_approving_when_every_persisted_gate_passed(tmp_db):
    from gaia.store.writer import (
        add_gate_to_task,
        read_task_gate_verdict,
        set_gate_status,
    )

    _seed_task(tmp_db)
    first = add_gate_to_task("me", _BRIEF, 1, "command",
                             evidence_shape="pytest -q", db_path=tmp_db)
    second = add_gate_to_task("me", _BRIEF, 1, "semantic",
                              evidence_shape="- the artifact is present",
                              db_path=tmp_db)

    # A freshly authored gate is 'pending': not approving yet.
    assert read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db).approving is False

    set_gate_status("me", _BRIEF, 1, first["gate_id"], "pass", db_path=tmp_db)
    assert read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db).approving is False

    set_gate_status("me", _BRIEF, 1, second["gate_id"], "pass", db_path=tmp_db)
    verdict = read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db)

    assert verdict.approving is True
    assert verdict.gate_count == 2
    assert verdict.status_counts == {"pass": 2}


def test_read_seam_is_not_approving_when_a_persisted_gate_failed(tmp_db):
    from gaia.store.writer import (
        add_gate_to_task,
        read_task_gate_verdict,
        set_gate_status,
    )

    _seed_task(tmp_db)
    passing = add_gate_to_task("me", _BRIEF, 1, "command",
                               evidence_shape="pytest -q", db_path=tmp_db)
    failing = add_gate_to_task("me", _BRIEF, 1, "code",
                               evidence_shape="ruff check", db_path=tmp_db)
    set_gate_status("me", _BRIEF, 1, passing["gate_id"], "pass", db_path=tmp_db)
    set_gate_status("me", _BRIEF, 1, failing["gate_id"], "fail", db_path=tmp_db)

    verdict = read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db)

    assert verdict.approving is False
    assert verdict.status_counts == {"pass": 1, "fail": 1}


def test_read_seam_on_a_task_with_zero_gates_is_not_approving(tmp_db):
    # The shape most live tasks are in: no gate was ever authored. The
    # derivation must never reach such a task with an approving verdict.
    from gaia.store.writer import list_task_gates, read_task_gate_verdict

    _seed_task(tmp_db)
    assert list_task_gates("me", _BRIEF, 1, db_path=tmp_db) == []

    verdict = read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db)

    assert verdict.approving is False
    assert verdict.gate_count == 0
    assert verdict.reasons == [EMPTY_GATE_SET_REASON]


def test_read_seam_writes_nothing_to_the_substrate(tmp_db):
    from gaia.store.writer import (
        add_gate_to_task,
        read_task_gate_verdict,
        set_gate_status,
    )

    _seed_task(tmp_db)
    gate = add_gate_to_task("me", _BRIEF, 1, "command",
                            evidence_shape="pytest -q", db_path=tmp_db)
    recorded = set_gate_status("me", _BRIEF, 1, gate["gate_id"], "pass",
                               db_path=tmp_db)

    before = _snapshot(tmp_db)
    verdict = read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db)
    after = _snapshot(tmp_db)

    # An approving verdict is the case where a write would be tempting, and the
    # primitive still declines it: deriving the verdict leaves the substrate
    # exactly as it found it, which is what before == after asserts.
    #
    # Closing IS someone else's decision, and that someone now exists -- it is
    # the seam that PERSISTS the verdict. set_gate_status above already carried
    # the task from 'pending' to 'done' by derivation, before this read ran. So
    # the task reads 'done' below not because the primitive wrote anything, but
    # because recording the verdict did, and the snapshot pair is what tells
    # those two apart.
    assert verdict.approving is True
    assert before == after
    derived = recorded["derived_closure"]
    assert derived["action"] == "close"
    assert derived["old_status"] == "pending"
    assert derived["new_status"] == "done"
    assert dict(zip(("id", "plan_id", "order_num", "status"),
                    after["tasks"][0]))["status"] == "done"


def test_read_seam_never_reaches_a_writer_entry_point(tmp_db, monkeypatch):
    # Behavioural proof rather than a promise in a docstring: if the read path
    # touched any state-advancing writer, this test would fail loudly.
    from gaia.store import writer

    _seed_task(tmp_db)
    gate = writer.add_gate_to_task("me", _BRIEF, 1, "command",
                                   evidence_shape="pytest -q", db_path=tmp_db)
    writer.set_gate_status("me", _BRIEF, 1, gate["gate_id"], "pass",
                           db_path=tmp_db)

    def _explode(*args, **kwargs):
        raise AssertionError("the derivation must not invoke a writer")

    monkeypatch.setattr(writer, "set_task_status", _explode)
    monkeypatch.setattr(writer, "set_gate_status", _explode)
    monkeypatch.setattr(writer, "add_gate_to_task", _explode)
    monkeypatch.setattr(writer, "remove_gate_from_task", _explode)
    monkeypatch.setattr(writer, "write_harness_event", _explode)

    assert writer.read_task_gate_verdict(
        "me", _BRIEF, 1, db_path=tmp_db
    ).approving is True


@pytest.mark.parametrize(
    "dispatch_agent",
    [None, "", "developer", "gaia-system", "gaia-orchestrator", "gaia-verifier"],
)
def test_read_seam_verdict_is_independent_of_the_dispatch_coordinate(
    tmp_db, monkeypatch, dispatch_agent
):
    # The seam that will invoke this must be interchangeable, which requires the
    # verdict to depend on the gate rows alone -- never on who is asking. The
    # writers' dispatch guard is deliberately not on this path.
    from gaia.store.writer import (
        add_gate_to_task,
        read_task_gate_verdict,
        set_gate_status,
    )

    _seed_task(tmp_db)
    gate = add_gate_to_task("me", _BRIEF, 1, "command",
                            evidence_shape="pytest -q", db_path=tmp_db)
    set_gate_status("me", _BRIEF, 1, gate["gate_id"], "pass", db_path=tmp_db)

    if dispatch_agent is None:
        monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    else:
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", dispatch_agent)

    verdict = read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db)

    assert verdict.approving is True
    assert verdict.status_counts == {"pass": 1}


def test_read_seam_is_idempotent_over_the_substrate(tmp_db):
    from gaia.store.writer import (
        add_gate_to_task,
        read_task_gate_verdict,
        set_gate_status,
    )

    _seed_task(tmp_db)
    gate = add_gate_to_task("me", _BRIEF, 1, "command",
                            evidence_shape="pytest -q", db_path=tmp_db)
    set_gate_status("me", _BRIEF, 1, gate["gate_id"], "pass", db_path=tmp_db)

    before = _snapshot(tmp_db)
    verdicts = [
        read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db) for _ in range(3)
    ]

    assert verdicts[0] == verdicts[1] == verdicts[2]
    assert _snapshot(tmp_db) == before


def test_read_seam_raises_for_an_unresolvable_task(tmp_db):
    from gaia.store.writer import read_task_gate_verdict

    _seed_task(tmp_db)

    # "this task does not exist" is not a verdict, so no verdict is fabricated
    # for it -- an absent task must not be readable as either answer.
    with pytest.raises(ValueError):
        read_task_gate_verdict("me", _BRIEF, 99, db_path=tmp_db)
    with pytest.raises(ValueError):
        read_task_gate_verdict("me", "no-such-brief", 1, db_path=tmp_db)


def test_read_seam_reads_the_shape_the_predicate_expects(tmp_db):
    # No translation layer between the read path and the derivation: the rows
    # list_task_gates returns are fed to the predicate as-is, so the two agree
    # on every persisted status without a mapping step to drift.
    from gaia.store.writer import (
        add_gate_to_task,
        list_task_gates,
        read_task_gate_verdict,
        set_gate_status,
    )

    _seed_task(tmp_db)
    for vtype in ("command", "code", "semantic", "self_review"):
        added = add_gate_to_task("me", _BRIEF, 1, vtype,
                                 evidence_shape="check", db_path=tmp_db)
        set_gate_status("me", _BRIEF, 1, added["gate_id"], "pass",
                        db_path=tmp_db)

    rows = list_task_gates("me", _BRIEF, 1, db_path=tmp_db)

    assert derive_gate_verdict(rows) == read_task_gate_verdict(
        "me", _BRIEF, 1, db_path=tmp_db
    )
    assert read_task_gate_verdict("me", _BRIEF, 1, db_path=tmp_db).gate_count == 4
