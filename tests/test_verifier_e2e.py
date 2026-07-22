"""B3 T5 (plan_id=33, task order_num=5, AC-5): the full DORMANT-mode
verification FLOW, end-to-end.

Matchable by ``pytest tests/ -k verifier_e2e -q``.

T1-T4 shipped four separate M1 pieces:
  * ``gaia.state.gate_oracle.run_oracle_check``           (T1: oracle mode)
  * ``skills/verification-rubric/scripts/rubric_verdict`` (T2: rubric mode)
  * ``gaia.store.writer.set_gate_status`` + the CLI verb  (T3: status write)
  * ``tests/fixtures/agents_staging/gaia-verifier.md``     (T4: staged agent)

Each was tested in isolation. T5 proves they COMPOSE: given a task whose
``task_gates`` are of MIXED ``verification_type`` (one deterministic, one
semantic), route each gate to its matching mode, persist the verdict via the
real T3 write-path, then transition the TASK ROW per the mapping F2 names --
``done`` when every gate passed, ``pending`` (left or returned -- rework)
when any gate failed. This is the exact sequence
``tests/fixtures/agents_staging/gaia-verifier.md`` describes in its
"Workflow" section (steps 1-3), reproduced here as a test-owned conductor
(``_run_verifier_flow`` below) so the sequence is exercised against the REAL
production pieces without landing or arming that agent.

``_run_verifier_flow`` is deliberately NOT a new shipped production module --
the staged agent's own workflow is tool-call driven (`gaia task gate list`
/ `set-status`, `gaia task set-status`), not a Python import of a shared
conductor. This function exists only so this test can drive the composed
pieces mechanically; nothing under ``agents/`` or ``build/`` references it.

Coverage, per AC-5:
  * ALL gates pass (one command-type via the oracle, one semantic-type via
    the rubric) -> task transitions ``pending`` -> ``done``.
  * ANY gate fails -> task STAYS ``pending`` (the gate-fail branch when the
    task was already pending -- "leave").
  * a rework scenario: task previously ``done``, a re-verification finds a
    failing gate -> task transitions back ``done`` -> ``pending`` (the
    literal "return to pending" half of the branch, exercising the actual
    state-machine edge, not just a no-op).
  * arming confirmation (updated at B3 M2): after driving this flow,
    ``verifier_fleet()`` against the LIVE ``agents/`` directory contains
    ``gaia-verifier`` and ``agents/gaia-verifier.md`` exists live -- M2 armed
    the registry; this flow's own dormant/armed distinction is orthogonal to
    that (see the two-level mapping note below) and is unaffected either way.

Two-level mapping note (plan finding F2, restated): this module only proves
the TASK ROW transition (``tasks.status`` pending/done, via
``gaia.store.writer.set_task_status``). The separate CONTRACT-level concern
-- ``agent_contract_handoff.plan_status`` COMPLETE vs NEEDS_VERIFICATION,
governed by the verifier-role gate -- is M2/T7 under ARMED enforcement, and
is out of scope here by design.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state.gate_oracle import (  # noqa: E402
    DETERMINISTIC_ORACLE_TYPES,
    run_oracle_check,
)

_PY = sys.executable

# rubric_verdict.py lives under skills/, which is not a Python package (same
# constraint documented in tests/skills/test_verifier_rubric.py) -- load it
# by path rather than import it.
_RUBRIC_SCRIPT = (
    _REPO_ROOT
    / "skills"
    / "verification-rubric"
    / "scripts"
    / "rubric_verdict.py"
)


def _load_rubric_verdict_module():
    spec = importlib.util.spec_from_file_location(
        "verification_rubric.rubric_verdict", _RUBRIC_SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_rubric_verdict = _load_rubric_verdict_module()
CriterionAssessment = _rubric_verdict.CriterionAssessment
assemble_verdict = _rubric_verdict.assemble_verdict
parse_rubric_criteria = _rubric_verdict.parse_rubric_criteria


# ---------------------------------------------------------------------------
# DB fixture -- isolated substrate per test, mirrors tests/cli/test_gate_
# status_write.py's tmp_db / _seed_task pattern (the established convention
# for exercising writer.py against a real, disposable sqlite file).
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_task(tmp_db: Path, brief: str, order_num: int = 1) -> None:
    """Seed workspace 'me' -> brief -> plan -> one pending task."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan, add_task_to_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, order_num, "verify this task's gates", db_path=tmp_db)


def _task_status(tmp_db: Path, brief: str, order_num: int) -> str:
    con = sqlite3.connect(str(tmp_db))
    try:
        row = con.execute(
            "SELECT t.status FROM tasks t "
            "JOIN plans p ON t.plan_id = p.id "
            "JOIN briefs b ON p.brief_id = b.id "
            "WHERE b.name = ? AND t.order_num = ?",
            (brief, order_num),
        ).fetchone()
        assert row is not None, f"no task row for brief={brief!r} order_num={order_num}"
        return row[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The conductor under test: wires T1 (oracle) + T2 (rubric) + T3 (write-path)
# together exactly as tests/fixtures/agents_staging/gaia-verifier.md's
# Workflow section describes (steps 1-3), then applies the task-row mapping
# from finding F2. `rubric_judgment` is the test's stand-in for the LLM-
# as-judge step rubric_verdict.py deliberately does not automate (see its
# module docstring) -- a dict of criterion-text -> met, applied to whichever
# semantic/self_review gate is being routed.
# ---------------------------------------------------------------------------

def _run_verifier_flow(
    workspace: str,
    brief: str,
    order_num: int,
    db_path: Path,
    rubric_judgment: dict[str, bool],
) -> bool:
    """Run every gate on the task through its matching mode, write each
    verdict, then transition the task row. Returns whether ALL gates passed.
    """
    from gaia.store.writer import list_task_gates, set_gate_status, set_task_status

    gates = list_task_gates(workspace, brief, order_num, db_path=db_path)
    assert gates, "no gates on task -- nothing to verify"

    for gate in gates:
        vtype = gate["verification_type"]

        if vtype in DETERMINISTIC_ORACLE_TYPES:
            # T1: oracle mode -- re-execute, never trust the prior claim.
            # list_task_gates already returns the exact {"verification_type",
            # "evidence_shape", ...} shape run_oracle_check expects: no
            # translation needed between the T3 read-path and the T1 oracle.
            oracle_verdict = run_oracle_check(gate)
            new_status = "pass" if oracle_verdict.ok else "fail"
        else:
            # T2: rubric mode -- split into criteria, judge each (via the
            # test-supplied stand-in), then assemble one justified verdict.
            criteria = parse_rubric_criteria(gate["evidence_shape"])
            assessments = [
                CriterionAssessment(
                    criterion=c,
                    met=rubric_judgment.get(c, True),
                    reasoning=(
                        f"observed: criterion {c!r} judged by the "
                        "test-supplied stand-in for the LLM-as-judge step"
                    ),
                )
                for c in criteria
            ]
            rubric_result = assemble_verdict(assessments)
            new_status = rubric_result.verdict
            # gaia.state.VALID_GATE_STATUSES == ("pending", "pass", "fail");
            # RubricVerdict.verdict is already exactly "pass"/"fail" -- no
            # translation needed at this seam either.

        set_gate_status(workspace, brief, order_num, gate["id"], new_status, db_path=db_path)

    updated_gates = list_task_gates(workspace, brief, order_num, db_path=db_path)
    all_pass = all(g["status"] == "pass" for g in updated_gates)

    if all_pass:
        set_task_status(workspace, brief, order_num, "done", db_path=db_path)
    else:
        # any-gate-fails branch: rework -- leave pending if already pending,
        # or return to pending if a prior cycle had marked it done. Never
        # advance to done on a partial pass.
        current = _task_status(db_path, brief, order_num)
        if current != "pending":
            set_task_status(workspace, brief, order_num, "pending", db_path=db_path)

    return all_pass


# ---------------------------------------------------------------------------
# Branch 1: ALL gates pass -> task transitions pending -> done.
# ---------------------------------------------------------------------------

def test_verifier_e2e_all_gates_pass_transitions_task_to_done(tmp_db):
    from gaia.store.writer import add_gate_to_task, list_task_gates

    brief = "verifier-e2e-all-pass"
    _seed_task(tmp_db, brief)

    add_gate_to_task(
        "me", brief, 1, "command",
        evidence_shape=f'{_PY} -c "import sys; sys.exit(0)"',
        db_path=tmp_db,
    )
    add_gate_to_task(
        "me", brief, 1, "semantic",
        evidence_shape=(
            "- the output names the exact input\n"
            "- the command exits cleanly\n"
        ),
        db_path=tmp_db,
    )

    assert _task_status(tmp_db, brief, 1) == "pending"

    all_pass = _run_verifier_flow(
        "me", brief, 1, tmp_db,
        rubric_judgment={
            "the output names the exact input": True,
            "the command exits cleanly": True,
        },
    )

    assert all_pass is True
    gates = list_task_gates("me", brief, 1, db_path=tmp_db)
    assert {g["verification_type"] for g in gates} == {"command", "semantic"}
    assert all(g["status"] == "pass" for g in gates)
    assert _task_status(tmp_db, brief, 1) == "done"


# ---------------------------------------------------------------------------
# Branch 2: ANY gate fails -> task STAYS pending (the "leave" half).
# ---------------------------------------------------------------------------

def test_verifier_e2e_any_gate_fails_task_stays_pending(tmp_db):
    from gaia.store.writer import add_gate_to_task, list_task_gates

    brief = "verifier-e2e-any-fail"
    _seed_task(tmp_db, brief)

    add_gate_to_task(
        "me", brief, 1, "code",
        evidence_shape=f'{_PY} -c "assert 1 == 1"',
        db_path=tmp_db,
    )
    add_gate_to_task(
        "me", brief, 1, "self_review",
        evidence_shape=(
            "- the change was tested\n"
            "- no regressions were introduced\n"
        ),
        db_path=tmp_db,
    )

    assert _task_status(tmp_db, brief, 1) == "pending"

    all_pass = _run_verifier_flow(
        "me", brief, 1, tmp_db,
        rubric_judgment={
            "the change was tested": True,
            "no regressions were introduced": False,  # the unmet criterion
        },
    )

    assert all_pass is False
    gates = list_task_gates("me", brief, 1, db_path=tmp_db)
    by_type = {g["verification_type"]: g["status"] for g in gates}
    assert by_type["code"] == "pass"
    assert by_type["self_review"] == "fail"
    # Both mode skills were genuinely invoked: one oracle pass, one rubric
    # fail -- the overall verdict is fail because ANY gate failed, not
    # because all did (verdict-inflation guard, mirrors rubric_verdict's own
    # single-unmet-criterion test).
    assert _task_status(tmp_db, brief, 1) == "pending"


# ---------------------------------------------------------------------------
# Branch 2b (rework): task was previously done; a re-verification finds a
# failing gate -> task genuinely transitions done -> pending (not a no-op).
# ---------------------------------------------------------------------------

def test_verifier_e2e_rework_returns_done_task_to_pending(tmp_db):
    from gaia.store.writer import add_gate_to_task, set_task_status, list_task_gates

    brief = "verifier-e2e-rework"
    _seed_task(tmp_db, brief)

    add_gate_to_task(
        "me", brief, 1, "command",
        evidence_shape=f'{_PY} -c "import sys; sys.exit(1)"',  # will fail
        db_path=tmp_db,
    )
    add_gate_to_task(
        "me", brief, 1, "semantic",
        evidence_shape="- the artifact is present\n",
        db_path=tmp_db,
    )

    # Simulate a prior cycle that had already closed the task.
    set_task_status("me", brief, 1, "done", db_path=tmp_db)
    assert _task_status(tmp_db, brief, 1) == "done"

    all_pass = _run_verifier_flow(
        "me", brief, 1, tmp_db,
        rubric_judgment={"the artifact is present": True},
    )

    assert all_pass is False
    gates = list_task_gates("me", brief, 1, db_path=tmp_db)
    by_type = {g["verification_type"]: g["status"] for g in gates}
    assert by_type["command"] == "fail"
    assert by_type["semantic"] == "pass"
    # The literal "return to pending" transition -- done -> pending, a real
    # state-machine move (gaia.state.transitions.TASK_LIFECYCLE_TRANSITIONS
    # explicitly allows "done" -> "pending" -- "allow reopen for retry"),
    # not merely leaving an already-pending task alone.
    assert _task_status(tmp_db, brief, 1) == "pending"


# ---------------------------------------------------------------------------
# Arming confirmation (updated at B3 M2): this task-row flow neither arms nor
# disarms the registry -- it operates one level below (task_gates/tasks), so
# the live registry state it observes is whatever M2 left it as: ARMED, with
# gaia-verifier present. Mirrors
# tests/test_verifier_agent_registry.py::TestLiveRegistryIsArmed.
# ---------------------------------------------------------------------------

class TestVerifierE2ELiveRegistryIsArmed:
    def test_live_verifier_fleet_contains_gaia_verifier_after_e2e_flow(self):
        from gaia.state import permissions as _permissions
        from gaia.state.permissions import verifier_fleet

        verifier_fleet.cache_clear()
        try:
            assert verifier_fleet() == frozenset({"gaia-verifier"})
        finally:
            verifier_fleet.cache_clear()

    def test_live_agents_dir_has_gaia_verifier_file(self):
        assert (_REPO_ROOT / "agents" / "gaia-verifier.md").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
