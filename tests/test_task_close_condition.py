"""The condition a task's closure has to satisfy, and the two ways to satisfy it.

Matchable by the three axes it covers::

    pytest tests/ -k "set_status_requires_gate or manual_override_records_event \
or skipped_stays_unconditional" -q

Three surfaces, one semantics:

  * ``gaia.state.task_closure_condition`` -- the pure decision (no DB, no env,
    no I/O), exercised as a truth table.
  * ``gaia.store.writer.set_task_status`` -- the single writer of
    ``tasks.status``, where the condition actually holds, against a real
    disposable sqlite substrate (``GAIA_DATA_DIR`` -> ``tmp_path``, the
    convention in tests/test_derived_closure_predicate.py and
    tests/test_close_override_event_channel.py).
  * ``bin/cli/task.py`` -- the operator's flags, so what is asserted is the
    surface a human types and not only the function beneath it.

What is asserted goes past the three enumerated axes, because the properties the
condition has to hold are stronger than its cases:

  * THE CONDITION IS IN THE WRITER, NOT THE CLI -- proven by closing through the
    writer directly, bypassing every flag, and being refused anyway. A condition
    that only holds at the command line does not hold for the seams still to be
    wired.
  * EVERY CELL OF THE PREDICATE IS DECIDED. The verdict x override truth table is
    enumerated exhaustively, and the three ``tasks.status`` values are classified
    exhaustively against ``VALID_TASK_STATUSES``, so no input can reach a
    permit-by-default fallthrough and no future status can inherit an exemption
    by omission.
  * A REFUSAL LEAVES NOTHING BEHIND -- not a status change, and not a
    half-written audit record.
  * AN OVERRIDE IS CONSUMED PER CLOSURE, NOT ONCE FOREVER. A task reopened after
    an override close cannot be re-closed on the strength of the old one.
  * THE RECORD PRECEDES THE MUTATION -- when the append fails, the task stays
    open. A closed task with no record is the silent escape hatch the channel
    exists to prevent, so the ordering is asserted by making the append fail.
  * AN OVERRIDE IS RECORDED ONLY WHEN IT WAS NEEDED, so every recorded override
    marks a task closed without an approving verdict rather than one that merely
    passed a redundant flag.
  * A MALFORMED OVERRIDE IS REJECTED EVEN WHEN IT WAS NOT NEEDED, and the
    rejection reuses the channel's own validator rather than a second one.
"""

from __future__ import annotations

import argparse
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

from gaia.state import VALID_TASK_STATUSES  # noqa: E402
from gaia.state.task_closure import (  # noqa: E402
    APPROVING_GATE_STATUS,
    GateVerdict,
    derive_gate_verdict,
)
from gaia.state.task_closure_condition import (  # noqa: E402
    CLOSING_STATUS,
    UNCONDITIONED_STATUSES,
    TaskClosureBlocked,
    build_closure_denial_message,
    closure_is_conditioned,
    decide_task_closure,
)
from gaia.state.task_closure_event import (  # noqa: E402
    MISSING_REASON_MESSAGE,
    TASK_CLOSE_OVERRIDE_EVENT,
    HUMAN_ACTOR,
)

_BRIEF = "close-condition-brief"
_WORKSPACE = "me"
_ORDER = 1
_REASON = "the gate's runner is offline on this machine; closing under protest"

# Reasons that state nothing. `None` is excluded deliberately: it is the value
# that means "no override requested", so it belongs to the refusal axis rather
# than to the malformed-argument one.
_EMPTY_REASONS = ["", "   ", "\n", "\t \n ", 42, 0, [], {}, b"why", object()]


# ---------------------------------------------------------------------------
# Substrate
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


def _seed(
    tmp_db: Path,
    gate_statuses: tuple[str, ...] = (),
    brief: str = _BRIEF,
    order_num: int = _ORDER,
) -> None:
    """Seed workspace -> brief -> plan -> one pending task with N gates."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import add_gate_to_task, add_task_to_plan, upsert_plan

    upsert_brief(_WORKSPACE, brief, {"status": "open", "title": brief},
                 db_path=tmp_db)
    upsert_plan(_WORKSPACE, brief, content="plan body", status="active",
                db_path=tmp_db)
    add_task_to_plan(_WORKSPACE, brief, order_num, "close this task",
                     db_path=tmp_db)
    for status in gate_statuses:
        add_gate_to_task(
            _WORKSPACE, brief, order_num, "command",
            evidence_shape="pytest -q", status=status, db_path=tmp_db,
        )


def _task_status(tmp_db: Path, brief: str = _BRIEF,
                 order_num: int = _ORDER) -> str:
    con = sqlite3.connect(str(tmp_db))
    try:
        row = con.execute(
            "SELECT t.status FROM tasks t "
            "JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.name = ? AND t.order_num = ?",
            (brief, order_num),
        ).fetchone()
        assert row is not None, f"task {order_num} of '{brief}' not seeded"
        return row[0]
    finally:
        con.close()


def _override_rows(tmp_db: Path) -> list[tuple]:
    """Every persisted override record, newest last."""
    con = sqlite3.connect(str(tmp_db))
    try:
        return con.execute(
            "SELECT id, workspace, ts, type, source, agent, result, severity, "
            "       payload FROM harness_events WHERE type = ? ORDER BY id",
            (TASK_CLOSE_OVERRIDE_EVENT,),
        ).fetchall()
    finally:
        con.close()


def _set_status_args(**overrides) -> argparse.Namespace:
    base = {
        "brief": _BRIEF,
        "task_id": str(_ORDER),
        "status": CLOSING_STATUS,
        "workspace": _WORKSPACE,
        "json": False,
        "override": False,
        "reason": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# The pure predicate: every cell decided
# ---------------------------------------------------------------------------

def _verdict(approving: bool) -> GateVerdict:
    return derive_gate_verdict(
        [{"id": 1, "status": APPROVING_GATE_STATUS if approving else "pending"}]
    )


@pytest.mark.parametrize(
    "approving,reason,permitted,override_used",
    [
        (True,  None,    True,  False),   # verified: closes on the evidence
        (True,  _REASON, True,  False),   # verified: the override is not consumed
        (False, _REASON, True,  True),    # unverified but accounted for
        (False, None,    False, False),   # backed by neither -> refused
    ],
)
def test_set_status_requires_gate_or_reason_in_every_cell_of_the_truth_table(
    approving, reason, permitted, override_used
):
    # Exhaustive over the two inputs: no combination reaches a fallthrough, so a
    # cell can never be permitted merely because nothing named it.
    decision = decide_task_closure(
        verdict=_verdict(approving),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        override_reason=reason,
    )
    assert decision.permitted is permitted
    assert decision.override_used is override_used
    # A record is owed exactly when the override carried the close.
    assert (decision.reason is not None) is override_used
    # And a refusal always says why; a permission never does.
    assert (decision.denial_message is not None) is (not permitted)


def test_set_status_requires_gate_and_a_zero_gate_task_is_never_approving():
    # The branch the primitive settled, reached through the decision: an empty
    # gate set carries no verdict, and no verdict is not an approving one.
    decision = decide_task_closure(
        verdict=derive_gate_verdict([]),
        brief_name=_BRIEF,
        task_order_num=_ORDER,
    )
    assert decision.permitted is False
    assert "zero gates" in decision.denial_message


def test_set_status_requires_gate_message_names_both_exits_and_what_is_missing():
    message = build_closure_denial_message(
        brief_name=_BRIEF,
        task_order_num=_ORDER,
        verdict=derive_gate_verdict(
            [{"id": 7, "status": "pending"}, {"id": 8, "status": "fail"}]
        ),
    )
    # What is missing, in the verdict's own terms.
    assert "have not passed" in message
    assert "pending=1" in message and "fail=1" in message
    # Exit 1: record the verdicts, so the closure derives from evidence.
    assert f"gaia task gate set-status {_BRIEF} {_ORDER}" in message
    assert APPROVING_GATE_STATUS in message
    # Exit 2: the override, with the flags that arm it and where it shows up.
    assert "--override" in message and "--reason=" in message
    assert TASK_CLOSE_OVERRIDE_EVENT in message
    assert "gaia defects" in message


@pytest.mark.parametrize("reason", _EMPTY_REASONS)
def test_manual_override_records_event_only_for_a_reason_that_states_something(
    reason
):
    # Rejected by the channel's own validator, reused rather than restated -- and
    # rejected even when the verdict would have closed the task anyway, so the
    # flag can never be learned as optional.
    for approving in (False, True):
        with pytest.raises(ValueError) as exc:
            decide_task_closure(
                verdict=_verdict(approving),
                brief_name=_BRIEF,
                task_order_num=_ORDER,
                override_reason=reason,
            )
        assert str(exc.value) == MISSING_REASON_MESSAGE


def test_skipped_stays_unconditional_and_every_status_is_classified():
    # The exemption is declared on both sides, so a fourth task status would
    # surface here as unclassified instead of silently inheriting it.
    assert set(UNCONDITIONED_STATUSES) | {CLOSING_STATUS} == set(VALID_TASK_STATUSES)
    assert CLOSING_STATUS not in UNCONDITIONED_STATUSES
    assert closure_is_conditioned(CLOSING_STATUS) is True
    for status in UNCONDITIONED_STATUSES:
        assert closure_is_conditioned(status) is False


# ---------------------------------------------------------------------------
# The writer: where the condition actually holds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "gate_statuses",
    [
        (),                          # no gates at all
        ("pending",),                # nothing verified yet
        ("fail",),                   # verified as broken
        ("pass", "pending"),         # partially verified
        ("pass", "fail"),            # one good verdict does not carry the rest
    ],
)
def test_set_status_requires_gate_verdict_in_the_writer_not_only_the_cli(
    tmp_db, gate_statuses
):
    # Called directly on the single writer of tasks.status, with no CLI flag in
    # sight: this is the property that makes the condition hold for the seams
    # still to be wired, not just for the one command that exists today.
    from gaia.store.writer import set_task_status

    _seed(tmp_db, gate_statuses)

    with pytest.raises(TaskClosureBlocked):
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                        db_path=tmp_db)

    # A refusal leaves nothing behind: not the status, not a record.
    assert _task_status(tmp_db) == "pending"
    assert _override_rows(tmp_db) == []


def test_set_status_requires_gate_verdict_and_an_approving_one_needs_no_override(
    tmp_db
):
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pass", "pass"))

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                          db_path=tmp_db)

    assert res["action"] == "updated"
    assert res["old_status"] == "pending" and res["new_status"] == "done"
    assert _task_status(tmp_db) == "done"
    # Derived closure is not an override, so it leaves no override record -- the
    # defect report stays a report of missing verification, not of every close.
    assert _override_rows(tmp_db) == []


def test_set_status_requires_gate_verdict_over_a_vocabulary_the_substrate_enforces(
    tmp_db
):
    # Why the derivation's off-vocabulary branch is defense in depth rather than
    # a reachable path: a near-miss like 'PASS' cannot be planted in the column
    # even by raw SQL. The condition therefore never has to decide whether a
    # look-alike counts, and this is the measurement that says so -- if the CHECK
    # were ever relaxed, this test is where that shows up.
    _seed(tmp_db, ("pass",))

    con = sqlite3.connect(str(tmp_db))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            con.execute("UPDATE task_gates SET status = 'PASS'")
    finally:
        con.close()

    assert _task_status(tmp_db) == "pending"


def test_set_status_requires_gate_verdict_but_a_noop_is_not_a_closure(tmp_db):
    # Re-issuing a close that has already happened transitions nothing, so there
    # is neither anything to justify nor anything to record. Demanding a fresh
    # override for a no-op would make the command non-idempotent for the seams
    # that will call it repeatedly.
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pending",))
    set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                    override_reason=_REASON, db_path=tmp_db)
    assert len(_override_rows(tmp_db)) == 1

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                          db_path=tmp_db)

    assert res["action"] == "noop"
    assert _task_status(tmp_db) == "done"
    assert len(_override_rows(tmp_db)) == 1


def test_manual_override_records_event_and_closes_the_task(tmp_db):
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pending", "fail"))

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                          override_reason=_REASON, db_path=tmp_db)

    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "done"

    rows = _override_rows(tmp_db)
    assert len(rows) == 1
    (_id, workspace, ts, ev_type, source, agent, result, severity,
     payload) = rows[0]
    assert workspace == _WORKSPACE
    assert ev_type == TASK_CLOSE_OVERRIDE_EVENT
    assert source == "cli"
    assert agent == HUMAN_ACTOR          # who
    assert ts                            # when
    assert _REASON in result             # why, visible without parsing JSON
    assert severity == "warning"
    assert _REASON in payload


def test_manual_override_records_event_with_the_dispatch_identity_as_actor(
    tmp_db, monkeypatch
):
    import json as _json

    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pending",))
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                    override_reason=_REASON, db_path=tmp_db)

    rows = _override_rows(tmp_db)
    assert len(rows) == 1
    assert rows[0][5] == "developer"     # the filterable agent column
    assert _json.loads(rows[0][8])["actor"] == "developer"


def test_manual_override_records_event_with_what_was_outstanding(tmp_db):
    import json as _json

    from gaia.state.task_closure_event import DETAILS_PAYLOAD_KEY
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pass", "pending", "fail"))

    set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                    override_reason=_REASON, db_path=tmp_db)

    payload = _json.loads(_override_rows(tmp_db)[0][8])
    assert payload["reason"] == _REASON
    assert payload["brief_name"] == _BRIEF
    assert payload["task_order_num"] == _ORDER
    details = payload[DETAILS_PAYLOAD_KEY]
    assert details["gate_count"] == 3
    assert details["gate_status_counts"] == {"pass": 1, "pending": 1, "fail": 1}
    assert details["verdict_reasons"]


def test_manual_override_records_event_only_when_the_override_was_needed(tmp_db):
    # An approving verdict satisfies the first disjunct, so the override is not
    # consumed and nothing is recorded. That is what keeps every recorded
    # override meaning "closed without an approving verdict".
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pass",))

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                          override_reason=_REASON, db_path=tmp_db)

    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "done"
    assert _override_rows(tmp_db) == []


@pytest.mark.parametrize("reason", _EMPTY_REASONS)
def test_manual_override_records_event_never_for_a_reason_stating_nothing(
    tmp_db, reason
):
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("pending",))

    with pytest.raises(ValueError) as exc:
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                        override_reason=reason, db_path=tmp_db)

    assert str(exc.value) == MISSING_REASON_MESSAGE
    assert _task_status(tmp_db) == "pending"
    assert _override_rows(tmp_db) == []


def test_manual_override_records_event_before_it_closes_the_task(
    tmp_db, monkeypatch
):
    # The ordering is the whole guarantee: if the append can fail after the
    # UPDATE, a task can end up closed with no record of why -- exactly the
    # silent escape hatch the channel exists to prevent. Asserted by making the
    # append fail and observing that the task stayed open.
    from gaia.store import writer

    _seed(tmp_db, ("pending",))

    def _explode(**kwargs):
        raise RuntimeError("substrate unavailable")

    monkeypatch.setattr(writer, "write_harness_event", _explode)

    with pytest.raises(RuntimeError, match="substrate unavailable"):
        writer.set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                               override_reason=_REASON, db_path=tmp_db)

    assert _task_status(tmp_db) == "pending"


def test_manual_override_records_event_once_per_closure_not_once_forever(tmp_db):
    # Reopening withdraws the closure; it does not carry the old justification
    # forward. A second close needs its own.
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("fail",))

    set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                    override_reason=_REASON, db_path=tmp_db)
    set_task_status(_WORKSPACE, _BRIEF, _ORDER, "pending", db_path=tmp_db)
    assert _task_status(tmp_db) == "pending"

    with pytest.raises(TaskClosureBlocked):
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                        db_path=tmp_db)
    assert len(_override_rows(tmp_db)) == 1

    set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                    override_reason="second attempt, same offline runner",
                    db_path=tmp_db)
    assert _task_status(tmp_db) == "done"
    assert len(_override_rows(tmp_db)) == 2


@pytest.mark.parametrize(
    "gate_statuses", [(), ("pending",), ("fail",), ("pass", "fail")]
)
def test_skipped_stays_unconditional_whatever_the_gates_say(
    tmp_db, gate_statuses
):
    from gaia.store.writer import set_task_status

    _seed(tmp_db, gate_statuses)

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, "skipped",
                          db_path=tmp_db)

    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "skipped"
    # Skipping asserts nothing about verification, so it is not an override and
    # leaves no record.
    assert _override_rows(tmp_db) == []


@pytest.mark.parametrize("reopen_from", ["done", "skipped"])
def test_skipped_stays_unconditional_and_so_does_reopening_to_pending(
    tmp_db, reopen_from
):
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("fail",))
    if reopen_from == "done":
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                        override_reason=_REASON, db_path=tmp_db)
    else:
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, "skipped", db_path=tmp_db)
    baseline = len(_override_rows(tmp_db))

    res = set_task_status(_WORKSPACE, _BRIEF, _ORDER, "pending", db_path=tmp_db)

    assert res["action"] == "updated"
    assert _task_status(tmp_db) == "pending"
    assert len(_override_rows(tmp_db)) == baseline


@pytest.mark.parametrize("status", list(UNCONDITIONED_STATUSES))
def test_skipped_stays_unconditional_so_an_override_on_it_is_refused(
    tmp_db, status
):
    # Refused rather than ignored: an operator who states a reason expects it
    # recorded, and dropping it silently would leave them believing an audit
    # record exists where none does.
    from gaia.store.writer import set_task_status

    _seed(tmp_db, ("fail",))
    if status == "pending":
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, CLOSING_STATUS,
                        override_reason=_REASON, db_path=tmp_db)
    baseline = len(_override_rows(tmp_db))

    with pytest.raises(ValueError) as exc:
        set_task_status(_WORKSPACE, _BRIEF, _ORDER, status,
                        override_reason=_REASON, db_path=tmp_db)

    assert "applies only to closing" in str(exc.value)
    assert len(_override_rows(tmp_db)) == baseline


# ---------------------------------------------------------------------------
# The CLI: the operator's two flags
# ---------------------------------------------------------------------------

def test_set_status_requires_gate_verdict_at_the_cli_too(tmp_db, capsys):
    from cli.task import _cmd_set_status

    _seed(tmp_db, ("pending",))

    rc = _cmd_set_status(_set_status_args())

    assert rc == 1
    err = capsys.readouterr().err
    assert "--override" in err and "--reason=" in err
    assert _task_status(tmp_db) == "pending"


def test_manual_override_records_event_from_the_cli_flags(tmp_db, capsys):
    from cli.task import _cmd_set_status

    _seed(tmp_db, ("pending",))

    rc = _cmd_set_status(_set_status_args(override=True, reason=_REASON))

    assert rc == 0
    assert "pending -> done" in capsys.readouterr().out
    assert _task_status(tmp_db) == "done"
    rows = _override_rows(tmp_db)
    assert len(rows) == 1
    assert _REASON in rows[0][6]


def test_manual_override_records_event_needs_a_reason_flag_beside_the_override(
    tmp_db, capsys
):
    from cli.task import _cmd_set_status

    _seed(tmp_db, ("pending",))

    rc = _cmd_set_status(_set_status_args(override=True, reason=None))

    assert rc == 1
    assert MISSING_REASON_MESSAGE in capsys.readouterr().err
    assert _task_status(tmp_db) == "pending"
    assert _override_rows(tmp_db) == []


def test_manual_override_records_event_refuses_a_reason_with_no_override_flag(
    tmp_db, capsys
):
    # The insidious half of the pairing: the writer takes a single
    # override_reason, so a reason with no flag to arm it would be dropped and
    # the close would be refused for a reason the operator thought they had
    # answered.
    from cli.task import _cmd_set_status

    _seed(tmp_db, ("pending",))

    rc = _cmd_set_status(_set_status_args(override=False, reason=_REASON))

    assert rc == 1
    assert "pass --override" in capsys.readouterr().err
    assert _task_status(tmp_db) == "pending"
    assert _override_rows(tmp_db) == []


def test_skipped_stays_unconditional_from_the_cli(tmp_db, capsys):
    from cli.task import _cmd_set_status

    _seed(tmp_db, ("fail",))

    rc = _cmd_set_status(_set_status_args(status="skipped"))

    assert rc == 0
    assert "pending -> skipped" in capsys.readouterr().out
    assert _task_status(tmp_db) == "skipped"
    assert _override_rows(tmp_db) == []
