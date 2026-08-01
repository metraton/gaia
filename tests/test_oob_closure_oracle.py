"""Out-of-band oracle for the derived task closure.

This suite exists because the change under test modifies the machinery that
verifies work, so it cannot be validated with that machinery. Every observation
here is made against ``tasks.status`` and ``harness_events`` through a
connection opened READ-ONLY (``file:...?mode=ro``), never through the read
helpers the closure path itself uses. A connection that cannot write cannot be
the thing that produced the state it reports.

The substrate is a per-test disposable sqlite file under ``tmp_path``; the live
database is never opened.

Matchable as one suite::

    pytest tests/test_oob_closure_oracle.py -q
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_WS = "oob-ws"
_BRIEF = "oob-oracle-brief"
_ORDER = 1


# ---------------------------------------------------------------------------
# The oracle: read-only observation, outside the flow under test
# ---------------------------------------------------------------------------

def _ro(db: Path) -> sqlite3.Connection:
    """Open the substrate READ-ONLY. Writing through it raises."""
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def observe_task_status(db: Path, order_num: int = _ORDER) -> str | None:
    """Raw SELECT of ``tasks.status``, through no production code path."""
    con = _ro(db)
    try:
        row = con.execute(
            "SELECT t.status FROM tasks t "
            "JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.name = ? AND t.order_num = ?",
            (_BRIEF, order_num),
        ).fetchone()
        return None if row is None else row[0]
    finally:
        con.close()


def observe_override_events(db: Path) -> list[tuple]:
    """Raw SELECT of every task.close_override row, oldest first."""
    con = _ro(db)
    try:
        return con.execute(
            "SELECT ts, type, source, agent, severity, result, payload "
            "FROM harness_events WHERE type = 'task.close_override' "
            "ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def observe_gate_statuses(db: Path) -> list[str]:
    con = _ro(db)
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT g.status FROM task_gates g "
                "JOIN tasks t ON t.id = g.task_id "
                "JOIN plans p ON p.id = t.plan_id "
                "JOIN briefs b ON b.id = p.brief_id "
                "WHERE b.name = ? AND t.order_num = ? ORDER BY g.id",
                (_BRIEF, _ORDER),
            ).fetchall()
        ]
    finally:
        con.close()


def _task_row_id(db: Path) -> int:
    con = _ro(db)
    try:
        row = con.execute(
            "SELECT t.id FROM tasks t "
            "JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.name = ? AND t.order_num = ?",
            (_BRIEF, _ORDER),
        ).fetchone()
        assert row is not None
        return row[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Ephemeral substrate
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path

    path = db_path()
    assert str(tmp_path) in str(path), "substrate escaped the temp dir"
    assert ".gaia" not in str(Path.home() / "x") or str(Path.home()) not in str(path)
    return path


def seed(db: Path, *, gate_count: int, bound_agent: str | None = None) -> list[int]:
    """Seed brief -> plan -> one pending task, N gates, optional dispatch binding.

    Uses only writers this brief did NOT change; the closure path
    (``set_task_status`` / ``set_gate_status``) is never used to build state.
    """
    from gaia.briefs import upsert_brief
    from gaia.store.writer import (
        add_gate_to_task,
        add_task_to_plan,
        insert_dispatched_handoff,
        upsert_plan,
    )

    upsert_brief(_WS, _BRIEF, {"status": "open", "title": _BRIEF}, db_path=db)
    upsert_plan(_WS, _BRIEF, content="body", status="active", db_path=db)
    add_task_to_plan(_WS, _BRIEF, _ORDER, "the task under oracle", db_path=db)

    gate_ids = [
        add_gate_to_task(
            _WS, _BRIEF, _ORDER, "command", evidence_shape="pytest -q", db_path=db
        )["gate_id"]
        for _ in range(gate_count)
    ]

    if bound_agent is not None:
        tid = _task_row_id(db)
        insert_dispatched_handoff(
            contract_id=f"dispatch.sess.{bound_agent}.{tid}",
            agent_id=bound_agent,
            workspace=_WS,
            plan_task_id=tid,
            kind="task_execution",
            session_id="sess",
            db_path=db,
        )
    return gate_ids


# ---------------------------------------------------------------------------
# 1. The oracle proper: pending BEFORE, done AFTER, both observed out of band
# ---------------------------------------------------------------------------

def test_oob_oracle_is_actually_read_only(db):
    """The instrument must be incapable of producing what it measures."""
    seed(db, gate_count=1)
    con = _ro(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE tasks SET status = 'done'")
    finally:
        con.close()


def test_oob_oracle_pending_before_done_after(db):
    from gaia.store.writer import set_gate_status

    gate_ids = seed(db, gate_count=2)

    # BEFORE -- observed out of band.
    assert observe_task_status(db) == "pending"
    assert observe_override_events(db) == []

    # A partial verdict must not close it.
    set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)
    assert observe_task_status(db) == "pending"

    # The completing verdict.
    set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[1], "pass", db_path=db)

    # AFTER -- observed out of band.
    assert observe_task_status(db) == "done"
    assert observe_gate_statuses(db) == ["pass", "pass"]


def test_oob_oracle_derived_close_leaves_no_override_event(db):
    """A derived close is verified, so it must NOT be marked unverified."""
    from gaia.store.writer import set_gate_status

    gate_ids = seed(db, gate_count=2)
    for gid in gate_ids:
        set_gate_status(_WS, _BRIEF, _ORDER, gid, "pass", db_path=db)

    assert observe_task_status(db) == "done"
    assert observe_override_events(db) == [], (
        "a derived close must leave zero task.close_override rows: one would "
        "brand an evidence-backed closure as closed-without-verification"
    )


def test_oob_oracle_failing_verdict_reopens_a_closed_task(db):
    from gaia.store.writer import set_gate_status

    gate_ids = seed(db, gate_count=2)
    for gid in gate_ids:
        set_gate_status(_WS, _BRIEF, _ORDER, gid, "pass", db_path=db)
    assert observe_task_status(db) == "done"

    res = set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[1], "fail", db_path=db)

    assert observe_task_status(db) == "pending"
    assert res["derived_closure"]["action"] == "reopen"


# ---------------------------------------------------------------------------
# 2. The most dangerous cell: zero gates must NEVER close
# ---------------------------------------------------------------------------

def test_oob_oracle_zero_gates_is_never_closed_by_the_api(db):
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.store.writer import set_task_status

    seed(db, gate_count=0)
    assert observe_task_status(db) == "pending"

    with pytest.raises(TaskClosureBlocked) as exc:
        set_task_status(_WS, _BRIEF, _ORDER, "done", db_path=db)

    assert observe_task_status(db) == "pending"
    assert "zero gates" in str(exc.value)
    assert observe_override_events(db) == []


def test_oob_oracle_zero_gates_is_unreachable_by_the_automatism(db):
    """There is no gate to record a verdict on, and the empty verdict refuses."""
    from gaia.state.task_closure import derive_gate_verdict
    from gaia.state.task_closure_derivation import (
        DerivedClosureAction,
        decide_derived_closure,
    )
    from gaia.state.task_closure_identity import ProducerStanding

    seed(db, gate_count=0)
    assert observe_gate_statuses(db) == []

    for standing in ProducerStanding:
        decision = decide_derived_closure(
            verdict=derive_gate_verdict([]),
            task_status="pending",
            standing=standing,
        )
        assert decision.action is DerivedClosureAction.NONE


# ---------------------------------------------------------------------------
# 3. The condition lives in the WRITER, not only in the CLI
# ---------------------------------------------------------------------------

def test_oob_oracle_api_close_with_unapproved_gates_is_rejected(db):
    """No CLI involved: a direct API caller is refused identically."""
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.store.writer import set_gate_status, set_task_status

    gate_ids = seed(db, gate_count=2)
    set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)
    assert observe_task_status(db) == "pending"

    with pytest.raises(TaskClosureBlocked) as exc:
        set_task_status(_WS, _BRIEF, _ORDER, "done", db_path=db)

    assert observe_task_status(db) == "pending"
    message = str(exc.value)
    assert "--override" in message and "gate set-status" in message


def test_oob_oracle_api_override_closes_and_records_exactly_one_event(db):
    from gaia.store.writer import set_task_status

    seed(db, gate_count=2)
    assert observe_task_status(db) == "pending"

    set_task_status(
        _WS, _BRIEF, _ORDER, "done",
        override_reason="  the gate runner is offline  ",
        db_path=db,
    )

    assert observe_task_status(db) == "done"
    rows = observe_override_events(db)
    assert len(rows) == 1
    ts, etype, source, agent, severity, result, payload = rows[0]
    assert ts and etype == "task.close_override" and source == "cli"
    assert agent == "human"
    assert severity == "warning"
    assert "the gate runner is offline" in result
    assert "the gate runner is offline" in payload
    assert '"actor": "human"' in payload or '"actor":"human"' in payload


def test_oob_oracle_override_event_is_visible_as_a_defect(db):
    from gaia.store.reader import read_defects
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1)
    set_task_status(
        _WS, _BRIEF, _ORDER, "done",
        override_reason="closing against the gates on purpose",
        db_path=db,
    )

    defects = read_defects(workspace=_WS, db_path=db)
    types = [d.get("type") for d in defects]
    assert "task.close_override" in types


# ---------------------------------------------------------------------------
# 4. AC-5, both branches
# ---------------------------------------------------------------------------

def test_oob_oracle_bound_producer_rejected_without_override(db):
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1, bound_agent="developer")

    import os
    os.environ["GAIA_DISPATCH_AGENT"] = "developer"
    try:
        with pytest.raises(TaskClosureBlocked) as exc:
            set_task_status(_WS, _BRIEF, _ORDER, "done", db_path=db)
    finally:
        os.environ.pop("GAIA_DISPATCH_AGENT", None)

    assert observe_task_status(db) == "pending"
    assert "ABSOLUTE" in str(exc.value)


def test_oob_oracle_bound_producer_rejected_with_override(db):
    """The refusal is absolute: --override does not lift it."""
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1, bound_agent="developer")

    import os
    os.environ["GAIA_DISPATCH_AGENT"] = "developer"
    try:
        with pytest.raises(TaskClosureBlocked) as exc:
            set_task_status(
                _WS, _BRIEF, _ORDER, "done",
                override_reason="I am closing my own task anyway",
                db_path=db,
            )
    finally:
        os.environ.pop("GAIA_DISPATCH_AGENT", None)

    assert observe_task_status(db) == "pending"
    assert "ABSOLUTE" in str(exc.value)
    assert observe_override_events(db) == [], (
        "a refused close must not leave an override record behind"
    )


def test_oob_oracle_bound_producer_cannot_close_by_automatism(db):
    from gaia.store.writer import set_gate_status

    gate_ids = seed(db, gate_count=1, bound_agent="developer")

    import os
    os.environ["GAIA_DISPATCH_AGENT"] = "developer"
    try:
        res = set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)
    finally:
        os.environ.pop("GAIA_DISPATCH_AGENT", None)

    assert observe_gate_statuses(db) == ["pass"]
    assert observe_task_status(db) == "pending"
    assert res["derived_closure"]["action"] == "none"


def test_oob_oracle_unlinked_close_is_not_free(db):
    """AC-5(b): no binding at all does NOT make the close free."""
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1, bound_agent=None)
    assert _no_binding_rows(db)

    with pytest.raises(TaskClosureBlocked) as exc:
        set_task_status(_WS, _BRIEF, _ORDER, "done", db_path=db)

    assert observe_task_status(db) == "pending"
    assert "NO dispatch binding names who produced this task" in str(exc.value)

    # ...and the only way through is the override with a reason.
    set_task_status(
        _WS, _BRIEF, _ORDER, "done",
        override_reason="no binding exists; closing on my own authority",
        db_path=db,
    )
    assert observe_task_status(db) == "done"
    assert len(observe_override_events(db)) == 1


def _no_binding_rows(db: Path) -> bool:
    con = _ro(db)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs WHERE plan_task_id = ?",
            (_task_row_id(db),),
        ).fetchone()[0]
        return n == 0
    finally:
        con.close()


def test_oob_oracle_unlinked_approving_verdict_needs_no_override(db):
    """The declared exemption: evidence alone closes an unlinked task."""
    from gaia.store.writer import set_gate_status

    gate_ids = seed(db, gate_count=1, bound_agent=None)
    set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)

    assert observe_task_status(db) == "done"
    assert observe_override_events(db) == []


# ---------------------------------------------------------------------------
# 5. AC-6, no regression on skipped
# ---------------------------------------------------------------------------

def test_oob_oracle_skipped_is_unconditional(db):
    from gaia.store.writer import set_task_status

    seed(db, gate_count=2)  # every gate pending
    assert observe_task_status(db) == "pending"

    set_task_status(_WS, _BRIEF, _ORDER, "skipped", db_path=db)

    assert observe_task_status(db) == "skipped"
    assert observe_override_events(db) == []


def test_oob_oracle_skipped_is_unconditional_even_for_a_bound_producer(db):
    from gaia.store.writer import set_task_status

    seed(db, gate_count=2, bound_agent="developer")

    import os
    os.environ["GAIA_DISPATCH_AGENT"] = "developer"
    try:
        set_task_status(_WS, _BRIEF, _ORDER, "skipped", db_path=db)
    finally:
        os.environ.pop("GAIA_DISPATCH_AGENT", None)

    assert observe_task_status(db) == "skipped"


def test_oob_oracle_override_on_a_non_closing_transition_is_refused(db):
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1)

    with pytest.raises(ValueError) as exc:
        set_task_status(
            _WS, _BRIEF, _ORDER, "skipped",
            override_reason="a reason nobody would record",
            db_path=db,
        )

    assert observe_task_status(db) == "pending"
    assert "applies only to closing a task" in str(exc.value)


# ---------------------------------------------------------------------------
# 6. Falsification attempts -- trying to break it, not to confirm it
# ---------------------------------------------------------------------------

def test_oob_falsify_blank_override_cannot_close(db):
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1)
    for blank in ("", "   ", "\t\n", 0, [], object()):
        with pytest.raises(ValueError):
            set_task_status(
                _WS, _BRIEF, _ORDER, "done", override_reason=blank, db_path=db
            )
    assert observe_task_status(db) == "pending"
    assert observe_override_events(db) == []


def test_oob_falsify_reissued_override_close_is_a_noop_without_a_new_event(db):
    """A second identical close must not manufacture a second audit record."""
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1)
    set_task_status(_WS, _BRIEF, _ORDER, "done",
                    override_reason="first close", db_path=db)
    assert len(observe_override_events(db)) == 1

    set_task_status(_WS, _BRIEF, _ORDER, "done",
                    override_reason="second close", db_path=db)

    assert observe_task_status(db) == "done"
    assert len(observe_override_events(db)) == 1, (
        "a no-op re-close must not append a second override record"
    )


def test_oob_falsify_removing_the_last_gate_does_not_reopen(db):
    """Known asymmetry: only set_gate_status derives, remove_gate does not."""
    from gaia.store.writer import remove_gate_from_task, set_gate_status

    gate_ids = seed(db, gate_count=1)
    set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)
    assert observe_task_status(db) == "done"

    remove_gate_from_task(_WS, _BRIEF, _ORDER, gate_ids[0], db_path=db)

    assert observe_gate_statuses(db) == []
    # Documenting the observed behaviour, not endorsing it: the task stays
    # 'done' with zero gates, a state the closure condition would now refuse.
    assert observe_task_status(db) == "done"


def test_oob_falsify_adding_a_gate_to_a_closed_task_does_not_reopen(db):
    from gaia.store.writer import add_gate_to_task, set_gate_status

    gate_ids = seed(db, gate_count=1)
    set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)
    assert observe_task_status(db) == "done"

    add_gate_to_task(_WS, _BRIEF, _ORDER, "semantic",
                     evidence_shape="judgement", db_path=db)

    assert observe_gate_statuses(db) == ["pass", "pending"]
    assert observe_task_status(db) == "done"


def test_oob_falsify_producer_message_is_degraded_by_a_malformed_reason(db):
    """Observation (a): normalize_reason runs before the absolute refusal.

    The close is still refused -- but with an argument error about the reason,
    not with the absolute producer refusal the identity module documents as
    coming first. Asserted as the OBSERVED behaviour so a future fix flips it.
    """
    from gaia.state.task_closure_condition import TaskClosureBlocked
    from gaia.state.task_closure_event import MISSING_REASON_MESSAGE
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1, bound_agent="developer")

    import os
    os.environ["GAIA_DISPATCH_AGENT"] = "developer"
    try:
        with pytest.raises(ValueError) as exc:
            set_task_status(
                _WS, _BRIEF, _ORDER, "done", override_reason="   ", db_path=db
            )
    finally:
        os.environ.pop("GAIA_DISPATCH_AGENT", None)

    assert observe_task_status(db) == "pending"
    assert str(exc.value) == MISSING_REASON_MESSAGE
    assert not isinstance(exc.value, TaskClosureBlocked)


def test_oob_falsify_a_distinct_agent_can_still_close_with_override(db):
    from gaia.store.writer import set_task_status

    seed(db, gate_count=1, bound_agent="developer")

    import os
    os.environ["GAIA_DISPATCH_AGENT"] = "gaia-verifier"
    try:
        set_task_status(
            _WS, _BRIEF, _ORDER, "done",
            override_reason="independent actor closing against the gates",
            db_path=db,
        )
    finally:
        os.environ.pop("GAIA_DISPATCH_AGENT", None)

    assert observe_task_status(db) == "done"
    rows = observe_override_events(db)
    assert len(rows) == 1
    assert rows[0][3] == "gaia-verifier"


# ---------------------------------------------------------------------------
# 7. The CLI surface the acceptance criteria actually name
# ---------------------------------------------------------------------------

def _run_set_status(order_num, status, *, override=False, reason=None):
    """Drive the real `gaia task set-status` handler in process."""
    import argparse

    _BIN = _REPO_ROOT / "bin"
    if str(_BIN) not in sys.path:
        sys.path.insert(0, str(_BIN))
    from cli.task import _cmd_set_status

    args = argparse.Namespace(
        brief=_BRIEF, task_id=str(order_num), status=status,
        override=override, reason=reason, workspace=_WS, json=False,
    )
    return _cmd_set_status(args)


def test_oob_oracle_cli_close_with_unapproved_gates_is_rejected(db, capsys):
    seed(db, gate_count=2)

    rc = _run_set_status(_ORDER, "done")

    assert rc != 0
    assert observe_task_status(db) == "pending"
    err = capsys.readouterr().err
    assert "--override" in err and "--reason" in err
    assert "gate set-status" in err


def test_oob_oracle_cli_override_closes_and_records_who_when_why(db, capsys):
    seed(db, gate_count=2)

    rc = _run_set_status(_ORDER, "done", override=True,
                         reason="the gate runner is offline")

    assert rc == 0
    assert observe_task_status(db) == "done"
    rows = observe_override_events(db)
    assert len(rows) == 1
    ts, _type, _src, agent, severity, result, payload = rows[0]
    assert ts                                  # WHEN
    assert agent == "human"                    # WHO
    assert "the gate runner is offline" in result   # WHY
    assert severity == "warning"


def test_oob_oracle_cli_override_without_reason_is_refused(db, capsys):
    seed(db, gate_count=1)

    rc = _run_set_status(_ORDER, "done", override=True, reason=None)

    assert rc != 0
    assert observe_task_status(db) == "pending"
    assert observe_override_events(db) == []


def test_oob_oracle_cli_reason_without_override_is_refused(db, capsys):
    """A reason with no flag to arm it would be silently dropped."""
    seed(db, gate_count=1)

    rc = _run_set_status(_ORDER, "done", override=False, reason="a reason")

    assert rc != 0
    assert observe_task_status(db) == "pending"
    assert observe_override_events(db) == []


def test_oob_oracle_cli_skipped_needs_no_gate_and_no_override(db, capsys):
    seed(db, gate_count=2)

    rc = _run_set_status(_ORDER, "skipped")

    assert rc == 0
    assert observe_task_status(db) == "skipped"
    assert observe_override_events(db) == []


# ---------------------------------------------------------------------------
# 8. The gap: the automatism is edge-triggered, never level-triggered
# ---------------------------------------------------------------------------

def test_oob_gap_all_gates_passing_does_not_converge_without_a_gate_write(db):
    """A task whose gates already all pass is NOT closed by anything.

    Nothing re-derives an existing gate state, so a task approved before the
    automatism existed -- or by any path other than set_gate_status -- stays
    pending until someone types a close. Observed against the substrate.
    """
    from gaia.store.writer import set_gate_status, set_task_status

    gate_ids = seed(db, gate_count=2)
    for gid in gate_ids:
        set_gate_status(_WS, _BRIEF, _ORDER, gid, "pass", db_path=db)
    assert observe_task_status(db) == "done"

    # Rewind the task by hand, leaving every gate at 'pass' -- the exact shape
    # of the 19 live tasks that sit pending with a complete approving verdict.
    set_task_status(_WS, _BRIEF, _ORDER, "pending", db_path=db)
    assert observe_task_status(db) == "pending"
    assert observe_gate_statuses(db) == ["pass", "pass"]

    # Re-reading the gates converges nothing: no reconciliation pass exists.
    from gaia.store.writer import list_task_gates
    list_task_gates(_WS, _BRIEF, _ORDER, db_path=db)
    assert observe_task_status(db) == "pending"

    # The recovery is clean, but it is a manual act: the approving verdict
    # carries the close on its own, with no override and no event.
    set_task_status(_WS, _BRIEF, _ORDER, "done", db_path=db)
    assert observe_task_status(db) == "done"
    assert observe_override_events(db) == []


def test_oob_a_failure_in_the_decision_half_is_reported_not_raised(db, monkeypatch):
    """Observation (b), once closed: the DECISION half is wrapped too.

    This test was written the other way round -- it asserted the escape, because
    the guard in ``_apply_derived_task_closure`` wrapped only the call to
    ``set_task_status`` and a failure in the decision that precedes it (or in
    the deferred imports above that) propagated out of ``set_gate_status`` AFTER
    the gate row had committed. The guard now spans the whole body, so the
    outcome the wrapping exists to prevent is prevented here as well: the
    verdict stands, the command succeeds, and the failure is reported.

    Kept as the oracle for that closure. The exhaustive per-step version lives
    in tests/test_derived_task_closure.py.
    """
    import gaia.state.task_closure_derivation as derivation
    from gaia.store.writer import DERIVED_CLOSURE_RESULT_KEY, set_gate_status

    gate_ids = seed(db, gate_count=1)

    def _boom(**_kwargs):
        raise RuntimeError("decision half failed")

    monkeypatch.setattr(derivation, "decide_derived_closure", _boom)

    result = set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)

    # The verdict IS recorded, and the command did not report failure for it.
    assert observe_gate_statuses(db) == ["pass"]
    assert observe_task_status(db) == "pending"
    derived = result[DERIVED_CLOSURE_RESULT_KEY]
    assert derived["action"] == "error"
    assert "decision half failed" in derived["error"]


def test_oob_control_a_failure_in_the_write_half_is_reported_not_raised(db,
                                                                       monkeypatch):
    """The control for the test above: the wrapped half behaves as documented."""
    import gaia.store.writer as writer
    from gaia.store.writer import set_gate_status

    gate_ids = seed(db, gate_count=1)
    real = writer.set_task_status

    def _boom(*_a, **_k):
        raise RuntimeError("write half failed")

    monkeypatch.setattr(writer, "set_task_status", _boom)
    try:
        res = set_gate_status(_WS, _BRIEF, _ORDER, gate_ids[0], "pass", db_path=db)
    finally:
        monkeypatch.setattr(writer, "set_task_status", real)

    assert res["derived_closure"]["action"] == "error"
    assert "write half failed" in res["derived_closure"]["error"]
    assert observe_gate_statuses(db) == ["pass"]
    assert observe_task_status(db) == "pending"


def test_oob_falsify_minted_agent_id_binding_does_not_bind(db):
    """A FINALIZED handoff stamps a minted id, which the guard drops as a name.

    Documented in producer_agent_names; observed here so the practical reach of
    the producer prohibition is on the record rather than assumed.
    """
    from gaia.state.task_closure_identity import (
        ProducerStanding,
        classify_producer_standing,
        producer_agent_names,
    )

    minted = "a" + "0123456789abcdef"
    names = producer_agent_names([{"agent_id": minted}])

    assert names == ()
    assert (
        classify_producer_standing(caller_agent=minted, producer_agents=names)
        is ProducerStanding.UNLINKED
    )
