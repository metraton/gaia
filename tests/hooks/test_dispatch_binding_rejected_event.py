"""
dispatch.binding_rejected -- audible signal for an ANOMALOUS nascent-row
birth rejection, with a discriminator that must stay silent for the
legitimate free-turn shape.

`_maybe_birth_dispatched_row` (hooks/adapters/claude_code.py) never blocks a
dispatch on a rejected binding -- that half is unchanged and covered by
tests/hooks/test_dispatch_referential_integrity.py. This file locks the
NEW half: which rejections land a `harness_events` row and which stay silent
on purpose.

Four reasons are unconditionally anomalous (some plan/verifier coordinate WAS
supplied and failed to resolve): `plan_task_id_unresolved`,
`plan_task_id_not_dispatchable`, `verifier_requires_parent_handoff_id`,
`parent_handoff_id_unresolved`. The fifth,
`task_execution_requires_plan_task_id`, is anomalous ONLY when `plan_id=<N>`
was extracted from the prompt (a plan-bound turn that dropped its `task_id=`
token) -- with no `plan_id=` either, that reason is the legitimate shape of a
free-standing dispatch (investigation, memory) and must emit NOTHING. That
negative case is the one that proves the discriminator, not the positive
cases alone. The anomaly channel is orthogonal to plan 49 task 1's D1 change
below -- it fires (or stays silent) purely off `reason` + `binding`, and
whether a row was ultimately BORN for that rejection never enters the
discriminator.

Plan 49 task 1 changes what those SAME five reasons mean for BIRTH, layered on
top of the (unchanged) anomaly discriminator:

  * `task_execution_requires_plan_task_id` can no longer be produced by a
    genuinely free dispatch (no `task_id=`, no `plan_id=`, no
    `parent_handoff_id=` at all): `extract_dispatch_binding` now classifies
    that shape as a FREE kind (`investigation` / `memory`, S1/S2) instead of
    forcing `task_execution`, so the row births normally through the
    unmodified happy path -- no rejection, no event, a REAL identity comes
    back. `test_free_dispatch_with_no_plan_signal_emits_nothing` and
    `test_free_dispatch_mentioning_a_number_that_is_not_the_token_emits_nothing`
    below assert exactly this. The reason is still reachable (and still
    anomalous under the SAME conditional) for the misdispatch shape --
    `plan_id=` named, `task_id=` dropped -- because ANY binding-shaped token
    keeps `task_execution` semantics (S2, conservative-by-design); see
    `test_task_execution_without_plan_task_id_but_with_plan_id_emits_event`.
  * `plan_task_id_unresolved` / `plan_task_id_not_dispatchable` (D1, gate 499)
    are now DEGRADED, not dropped: the row still births, with `plan_task_id`
    NULL in the column and the rejection reason + the failed token recorded
    inside the birth envelope (`binding_rejection`), consultable via
    `gaia contract list --json`. The anomaly event still fires exactly as
    before -- degrading does not silence it.
  * The two verifier reasons (`verifier_requires_parent_handoff_id`,
    `parent_handoff_id_unresolved`) are OUT of D1's scope and keep today's
    behavior unchanged: no row, no identity, same as before this task.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for _p in (str(_HOOKS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    DISPATCH_BINDING_REJECTED_EVENT,
    ClaudeCodeAdapter,
    _is_anomalous_dispatch_binding_rejection,
)
from gaia.paths import db_path  # noqa: E402
from gaia.store import writer as _store_writer  # noqa: E402

WORKSPACE = "me"
PLAN_ID = 41
TASK_PENDING = 71   # dispatchable
TASK_DONE = 72      # terminal -> not dispatchable
MISSING_TASK = 9999  # never seeded -> unresolved
MISSING_PARENT = 8888  # never seeded -> unresolved


def _seed_plan_tasks() -> None:
    """briefs(1) -> plans(PLAN_ID) -> tasks(TASK_PENDING pending, TASK_DONE done).

    Goes through the writer's own ``_connect`` (not a bare ``sqlite3.connect``)
    so the schema is materialized first -- mirrors the seed helper in
    tests/hooks/test_dispatch_referential_integrity.py.
    """
    con = _store_writer._connect(db_path())
    try:
        _store_writer._ensure_workspace_row(con, WORKSPACE)
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "binding-rejected-event", "in-progress"),
        )
        con.execute(
            "INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
            (PLAN_ID, 1, "active"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) VALUES (?,?,?,?,?)",
            (TASK_PENDING, PLAN_ID, 1, "some task", "pending"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) VALUES (?,?,?,?,?)",
            (TASK_DONE, PLAN_ID, 2, "closed task", "done"),
        )
        con.commit()
    finally:
        con.close()


def _seed_producer_handoff() -> int:
    """A real finalized row a verifier dispatch can legitimately point at."""
    result = _store_writer.finalize_agent_contract_handoff(
        contract_id="aproducer0000000.parent",
        agent_id="aproducer0000000",
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json="{}",
        db_path=db_path(),
    )
    return result["handoff_id"]


def _rows(event_type: str = DISPATCH_BINDING_REJECTED_EVENT) -> list:
    con = _store_writer._connect(db_path())
    try:
        return con.execute(
            "SELECT type, severity, agent, result, payload FROM harness_events "
            "WHERE type = ?",
            (event_type,),
        ).fetchall()
    finally:
        con.close()


def _birth(prompt: str, agent_name: str = "gaia-system"):
    """Drive the real dispatch-side entry point, exactly as the hook calls it."""
    parameters = {"prompt": prompt, "workspace": WORKSPACE}
    return ClaudeCodeAdapter._maybe_birth_dispatched_row(
        parameters, agent_name, "sess-binding-event",
    )


def _fetch_row(contract_id: str) -> dict:
    """Read the born row back by contract_id (plan 49 task 1 assertions)."""
    con = _store_writer._connect(db_path())
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT plan_task_id, kind, raw_handoff_json FROM agent_contract_handoffs "
            "WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return dict(row) if row is not None else {}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The discriminator itself, at the unit level -- fast, precise, no DB needed.
# ---------------------------------------------------------------------------

class TestDiscriminatorUnit:
    def test_always_anomalous_reasons_are_anomalous_regardless_of_binding(self):
        for reason in (
            "plan_task_id_unresolved",
            "plan_task_id_not_dispatchable",
            "verifier_requires_parent_handoff_id",
            "parent_handoff_id_unresolved",
        ):
            assert _is_anomalous_dispatch_binding_rejection(reason, {}) is True
            assert _is_anomalous_dispatch_binding_rejection(
                reason, {"plan_id": 1},
            ) is True

    def test_task_execution_reason_is_conditional_on_plan_id(self):
        reason = "task_execution_requires_plan_task_id"
        assert _is_anomalous_dispatch_binding_rejection(
            reason, {"plan_id": None},
        ) is False, "no plan_id extracted -- the legitimate free-turn shape"
        assert _is_anomalous_dispatch_binding_rejection(
            reason, {"plan_id": 41},
        ) is True, "plan_id was named -- the task_id= token was dropped"

    def test_unknown_reason_defaults_silent(self):
        # A reason this discriminator has never seen is not assumed anomalous --
        # only the four named codes (plus the conditional fifth) trip it.
        assert _is_anomalous_dispatch_binding_rejection("some_future_reason", {}) is False


# ---------------------------------------------------------------------------
# Positive half: each anomalous reason lands exactly one warning-severity row.
# ---------------------------------------------------------------------------

class TestAnomalousRejectionsAreAudible:
    def test_plan_task_id_unresolved_emits_event_and_degrades(self):
        """D1 (gate 499): the row is now DEGRADED, not dropped -- identity comes
        back, plan_task_id is NULL in the column (referential integrity is not
        weakened), and the rejection is recorded inside the birth envelope. The
        anomaly event still fires exactly as before -- degrading does not
        silence it."""
        _seed_plan_tasks()
        identity = _birth(f"do the thing task_id={MISSING_TASK}")
        assert identity is not None, "D1: an unresolved task_id= degrades, not drops"

        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert rows[0]["agent"] == "gaia-system"
        assert "plan_task_id_unresolved" in rows[0]["payload"]

        row = _fetch_row(identity["contract_id"])
        assert row["plan_task_id"] is None, "the FK forbids sealing an unresolved coordinate"
        assert row["kind"] == "task_execution", "the ATTEMPTED kind is preserved"
        assert "plan_task_id_unresolved" in row["raw_handoff_json"]
        assert str(MISSING_TASK) in row["raw_handoff_json"], (
            "the failed token itself is consultable in the birth envelope"
        )

    def test_plan_task_id_not_dispatchable_emits_event_and_degrades(self):
        _seed_plan_tasks()
        identity = _birth(f"do the thing task_id={TASK_DONE}")
        assert identity is not None, "D1: a non-dispatchable task_id= degrades, not drops"

        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert "plan_task_id_not_dispatchable" in rows[0]["payload"]

        row = _fetch_row(identity["contract_id"])
        assert row["plan_task_id"] is None
        assert "plan_task_id_not_dispatchable" in row["raw_handoff_json"]
        assert str(TASK_DONE) in row["raw_handoff_json"]

    def test_verifier_requires_parent_handoff_id_emits_event(self):
        _seed_plan_tasks()
        identity = _birth("verify the producer's work", agent_name="gaia-verifier")
        assert identity is None

        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert rows[0]["agent"] == "gaia-verifier"
        assert "verifier_requires_parent_handoff_id" in rows[0]["payload"]

    def test_parent_handoff_id_unresolved_emits_event(self):
        _seed_plan_tasks()
        identity = _birth(
            f"verify parent_handoff_id={MISSING_PARENT}", agent_name="gaia-verifier",
        )
        assert identity is None

        rows = _rows()
        assert len(rows) == 1
        assert "parent_handoff_id_unresolved" in rows[0]["payload"]

    def test_task_execution_without_plan_task_id_but_with_plan_id_emits_event(self):
        """plan_id=<N> named, task_id= dropped -- the session-47 misdispatch shape."""
        _seed_plan_tasks()
        identity = _birth(f"do the thing plan_id={PLAN_ID}")
        assert identity is None

        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert "task_execution_requires_plan_task_id" in rows[0]["payload"]
        assert f'"plan_id":{PLAN_ID}' in rows[0]["payload"]

    def test_verifier_dispatch_still_binds_to_producer_and_emits_nothing(self):
        """Sanity: a SOUND binding births the row and stays silent -- the event
        channel is for rejections only, never for a successful birth."""
        _seed_plan_tasks()
        parent_id = _seed_producer_handoff()
        identity = _birth(
            f"verify parent_handoff_id={parent_id}", agent_name="gaia-verifier",
        )
        assert identity is not None
        assert _rows() == []


# ---------------------------------------------------------------------------
# Negative half: the legitimate free turn stays silent. This is the half that
# proves the discriminator -- without it, "anomalous" means nothing.
# ---------------------------------------------------------------------------

class TestLegitimateFreeTurnStaysSilent:
    def test_free_dispatch_with_no_plan_signal_emits_nothing(self):
        """No task_id=, no plan_id=, no parent_handoff_id= -- an ordinary
        investigation/memory dispatch. Plan 49 task 1: this is no longer
        forced through task_execution and rejected -- it classifies as a FREE
        kind (S1/S2) and births NORMALLY through the unmodified happy path, so
        there is nothing to be anomalous about and the event channel stays
        silent for the same reason it always did (no rejection at all now,
        not merely a legitimate one)."""
        _seed_plan_tasks()
        identity = _birth("investigate why the build is flaky")
        assert identity is not None, "a free turn now births its own row (AC-1, gate 492)"

        row = _fetch_row(identity["contract_id"])
        assert row["plan_task_id"] is None
        assert row["kind"] == "investigation"

        assert _rows() == [], (
            "a genuinely free turn births cleanly -- no DispatchBindingError is "
            "even raised, so there is nothing for the anomaly channel to report"
        )

    def test_free_dispatch_mentioning_a_number_that_is_not_the_token_emits_nothing(self):
        """Prose alone ('task 6') never satisfies the parser -- covered already by
        extract_dispatch_binding's own regex, but re-asserted here at the event
        boundary: no token match means no binding-shaped token was found at
        all, so this classifies as a free turn and births cleanly, same as
        the no-signal case above."""
        _seed_plan_tasks()
        identity = _birth("please handle task 6 of the plan for me")
        assert identity is not None

        row = _fetch_row(identity["contract_id"])
        assert row["kind"] == "investigation"

        assert _rows() == []

    def test_free_dispatch_naming_memory_skill_classifies_as_memory(self):
        """S1: the literal `Skill('memory')` marker every memory dispatch
        carries (agents/gaia-orchestrator.md, agents/gaia-operator.md) is the
        one signal that tips a free turn's kind to 'memory' instead of the
        'investigation' default."""
        _seed_plan_tasks()
        identity = _birth("Carga `Skill('memory')`. Guarda esta nota para el usuario.")
        assert identity is not None

        row = _fetch_row(identity["contract_id"])
        assert row["plan_task_id"] is None
        assert row["kind"] == "memory"

        assert _rows() == []

    def test_successful_task_execution_birth_emits_nothing(self):
        """The ordinary, non-rejected path: no event at all, positive or negative."""
        _seed_plan_tasks()
        identity = _birth(f"do the thing task_id={TASK_PENDING} plan_id={PLAN_ID}")
        assert identity is not None
        assert _rows() == []


# ---------------------------------------------------------------------------
# Non-blocking contract: a write failure must never surface to the dispatch.
# ---------------------------------------------------------------------------

class TestNonBlocking:
    def test_event_write_failure_does_not_change_the_degraded_birth_outcome(self, monkeypatch):
        """The anomaly-event write and the degraded-row write are independent
        best-effort paths -- a failure in one must never affect the other."""
        import modules.events.event_writer as event_writer

        def _boom(*_args, **_kwargs):
            raise RuntimeError("substrate unavailable")

        monkeypatch.setattr(event_writer.EventWriter, "write_event", _boom)

        _seed_plan_tasks()
        identity = _birth(f"do the thing task_id={MISSING_TASK}")
        assert identity is not None, "the degraded birth is unaffected by the event failure"
        assert _rows() == [], "the event write itself still failed, silently"

        row = _fetch_row(identity["contract_id"])
        assert row["plan_task_id"] is None

    def test_degraded_birth_write_failure_still_does_not_block_the_dispatch(self, monkeypatch):
        """The mirror case: if the DEGRADE write itself fails, the dispatch is
        still never blocked -- the turn simply runs unbound, same as today's
        behavior for a rejection that is not degraded."""
        from modules.agents import dispatch_binding as dispatch_binding_module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("writer unavailable")

        monkeypatch.setattr(dispatch_binding_module, "birth_degraded_row", _boom)

        _seed_plan_tasks()
        identity = _birth(f"do the thing task_id={MISSING_TASK}")
        assert identity is None, "a failed degrade attempt falls back to no row, never a block"
