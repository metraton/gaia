"""End-to-end parametrized eval suite.

Ties together :mod:`tests.evals.catalog`, :mod:`tests.evals.runner`,
:mod:`tests.evals.graders`, and :mod:`tests.evals.reporter`. Loads the
shipped ``context_consumption.yaml``, parametrizes over every case, and
grades each response against its declared grader(s). Accumulated case
results are written to ``tests/evals/results/{run_id}.json`` at session
teardown and diffed against the committed baseline; a case that scored
BELOW its baseline fails the run (see :func:`reporter.enforce_no_regression`).

One dispatch strategy: a case whose backend is ``routing_sim`` runs the
real :class:`tests.evals.runner.RoutingSimBackend` on every ``pytest``
invocation. The simulator is synchronous and spends no tokens, so the case
measures the real router for free.

Two strategies this module used to carry are gone, for opposite reasons.
A ``FakeBackend`` "smoke" path graded each case against a canned response
written by this very module -- it grades the fixture, not the system, and
cannot fail for any behaviour whatsoever;
:func:`test_cases_run_real_machinery` now forbids reintroducing it. A live
``-m llm`` path dispatched ``claude --print --agent <specialist>``, which
skips the orchestrator every real session is rooted in and therefore
measured the bypass of Gaia's flow rather than the flow.

Routing of grader DSL by catalog ``grader`` list entry:

* ``code_grader`` -- reads ``stdout``, matches ``expect_present`` /
  ``expect_absent``.
* ``tool_trace_grader`` -- walks ``DispatchResult.session_path`` +
  ``audit_paths`` for ordering / presence / absence / repeat-count.
* ``routing_grader`` -- parses ``stdout`` as serialized ``RoutingResult``
  (paired with the routing-sim backend).

This test MUST NOT modify ``runner.py`` / ``graders.py`` / ``reporter.py`` /
``catalog.py`` -- it consumes them as stable APIs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.evals.catalog import CaseModel, load_catalog
from tests.evals.graders import (
    GradeResult,
    code_grader,
    routing_grader,
    skill_injection_consumer,
    tool_trace_grader,
)
from tests.evals.reporter import (
    compare_to_baseline,
    enforce_no_regression,
    load_baseline,
    save_result,
    write_baseline_candidate,
)
from tests.evals.runner import (
    DispatchResult,
    RoutingSimBackend,
    dispatch,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EVALS_DIR = Path(__file__).resolve().parent
_CATALOG_PATH = _EVALS_DIR / "catalogs" / "context_consumption.yaml"
_RESULTS_DIR = _EVALS_DIR / "results"
_BASELINE_PATH = _RESULTS_DIR / "baseline.json"


# ---------------------------------------------------------------------------
# Routing DB seeding (S4 / RoutingSimBackend)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _seeded_routing_db(tmp_path, monkeypatch):
    """Seed a temp gaia.db with the DB-backed ``surface_routing`` table.

    S4 dispatches through the real :class:`tests.evals.runner.RoutingSimBackend`,
    which builds a real ``RoutingSimulator``. Routing is DB-backed since the
    routing migration retired ``config/surface-routing.json`` in favor of
    ``tools/scan/seed_surface_routing.py`` seeding a ``surface_routing`` table
    from agent frontmatter (see ``tools/context/surface_router.py::load_surface_routing_config``).

    Without this fixture, ``tests/conftest.py``'s autouse
    ``_isolate_gaia_data_dir`` fixture points ``GAIA_DATA_DIR`` at a fresh,
    unseeded tmp dir for every test, so ``load_surface_routing_config``
    degrades to the empty/degraded config and S4 falls back to the
    reconnaissance agent (``developer``) instead of routing to
    ``gitops-operator`` / ``cloud-troubleshooter``.

    Mirrors ``tests/evals/test_backend_routing.py::_seeded_routing_db``
    exactly (same helpers, same shape) -- that fixture already proves this
    seeding produces the correct S4 routing outcome.
    """
    from tests.fixtures.db_helpers import (
        bootstrap_gaia_schema,
        seed_surface_routing_from_agents,
    )

    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    seed_surface_routing_from_agents(db)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))


# ---------------------------------------------------------------------------
# Grader dispatch
# ---------------------------------------------------------------------------


def _combine_results(graders_results: list[GradeResult]) -> GradeResult:
    """Merge multiple grader outcomes into a single :class:`GradeResult`.

    For a case declaring multiple graders we require EVERY grader to pass.
    The merged score is the arithmetic mean so semantic + binary
    combinations degrade gracefully.
    """
    if not graders_results:
        return GradeResult(passed=True, score=1.0, reasons=["no graders declared"])
    passed = all(g.passed for g in graders_results)
    score = sum(g.score for g in graders_results) / len(graders_results)
    reasons: list[str] = []
    for g in graders_results:
        reasons.extend(g.reasons)
    return GradeResult(passed=passed, score=score, reasons=reasons)


def _grade_case(
    case: CaseModel,
    result: DispatchResult,
) -> GradeResult:
    """Route ``result`` through every grader declared by ``case``.

    The routing mirrors the catalog ``grader`` list literally -- a case
    with two graders runs both, and their outcomes are merged via
    :func:`_combine_results` (logical AND on ``passed``, mean on ``score``).
    """
    outcomes: list[GradeResult] = []
    for name in case.grader:
        if name == "code_grader":
            outcomes.append(
                code_grader(
                    result.stdout,
                    expect_present=case.expect_present,
                    expect_absent=case.expect_absent,
                )
            )
        elif name == "tool_trace_grader":
            outcomes.append(
                tool_trace_grader(
                    session_path=result.session_path,
                    audit_paths=list(result.audit_paths),
                    trace_expect=case.trace_expect,
                )
            )
        elif name == "routing_grader":
            outcomes.append(
                routing_grader(result.stdout, routing_expect=case.routing_expect)
            )
        elif name == "skill_injection_consumer":
            outcomes.append(
                skill_injection_consumer(
                    audit_paths=list(result.audit_paths),
                    anomaly_expect=case.anomaly_expect,
                )
            )
        else:  # pragma: no cover - catalog loader guards this
            pytest.fail(f"unknown grader for case {case.id}: {name!r}")
    return _combine_results(outcomes)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch_case(case: CaseModel) -> DispatchResult:
    """Dispatch ``case`` through the backend its catalog entry declares.

    ``routing_sim`` is the only backend this suite dispatches: it runs the
    real simulator in-process, synchronously and for free. A case declaring
    anything else is rejected by :func:`test_cases_run_real_machinery`
    rather than silently reaching a backend that cannot answer it.
    """
    if case.backend != "routing_sim":
        pytest.fail(
            f"case {case.id} declares backend {case.backend!r}, which this "
            "suite cannot dispatch"
        )
    return dispatch(
        agent_type=case.agent, task=case.task, backend=RoutingSimBackend()
    )


# ---------------------------------------------------------------------------
# Load the catalog once per session
# ---------------------------------------------------------------------------


_ALL_CASES = load_catalog(_CATALOG_PATH)


def _case_ids(cases: list[CaseModel]) -> list[str]:
    return [c.id for c in cases]


# ---------------------------------------------------------------------------
# Session-scoped accumulator + reporter teardown
# ---------------------------------------------------------------------------


class _RunRecorder:
    """Collects per-case results and flushes them to disk at teardown.

    Session-scoped: instantiated once at the first parametrized test, then
    fed by every case that runs. At the end of the session (via the
    :func:`_recorder` fixture's finalizer) it writes the run payload
    through :func:`reporter.save_result`, diffs against the committed
    baseline, and FAILS the session on a regression.
    """

    def __init__(self, catalog_name: str) -> None:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"{now}-evals"
        self.catalog_name = catalog_name
        self.cases: list[dict] = []

    def record(
        self,
        case: CaseModel,
        grade: GradeResult,
        response_snippet: str,
    ) -> None:
        self.cases.append({
            "id": case.id,
            "agent": case.agent,
            "scoring": case.scoring,
            "passed": bool(grade.passed),
            "score": float(grade.score),
            "reasons": list(grade.reasons),
            "response_snippet": response_snippet[:280],
        })

    def flush(self) -> tuple[Path, object]:
        """Write the run JSON and return ``(path, drift_report)``.

        Idempotent under repeated calls: ``save_result`` overwrites the
        same ``{run_id}.json`` since the run id is computed once at
        construction.
        """
        payload = {
            "run_id": self.run_id,
            "catalog": self.catalog_name,
            "cases": list(self.cases),
        }
        path = save_result(self.run_id, payload, results_dir=_RESULTS_DIR)
        drift = compare_to_baseline(payload)
        return path, drift


@pytest.fixture(scope="session")
def _recorder() -> "_RunRecorder":
    """One recorder per pytest session; the baseline gate lives in its teardown.

    Gating at teardown rather than in a final test is deliberate: the diff
    is only meaningful once every case that is going to run has run, and
    pytest gives no ordering guarantee that would let a plain test observe
    that moment.

    Partial runs are safe. ``compare_to_baseline`` only walks the cases the
    run actually recorded, so ``-k S4`` compares S4 and stays silent about
    the rest rather than reporting the unrun cases as regressions.
    """
    recorder = _RunRecorder("context_consumption.yaml")
    yield recorder

    path, drift = recorder.flush()
    print(f"[evals] wrote run results to {path}")
    print(
        f"[evals] baseline: has_drift={drift.has_drift} "
        f"has_regression={drift.has_regression} entries={len(drift.entries)}"
    )

    # An improvement is still drift, and leaving it unpromoted lets the
    # baseline sag until a later regression back to the old value passes
    # unnoticed. Writing the candidate makes promotion a one-line `mv`, so
    # the easy path is the correct one. Never written over a regression --
    # a candidate sitting next to a red run is an invitation to promote the
    # regression.
    if drift.has_drift and not drift.has_regression:
        candidate = write_baseline_candidate(
            {"cases": recorder.cases},
            path=_RESULTS_DIR / "baseline.candidate.json",
        )
        print(
            f"[evals] scores improved over baseline; candidate written to "
            f"{candidate} -- promote with `mv` after review"
        )

    enforce_no_regression(drift)


# ---------------------------------------------------------------------------
# Case parametrization -- every case runs on every pytest invocation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    _ALL_CASES,
    ids=_case_ids(_ALL_CASES),
)
def test_catalog_case(case: CaseModel, _recorder: _RunRecorder) -> None:
    """Run every catalog case; all of them cost nothing.

    Today that is the routing simulator: a synchronous, in-process
    classifier reading the same DB-backed ``surface_routing`` table the
    orchestrator reads, so the case exercises production routing rather
    than a stand-in.
    """
    result = _dispatch_case(case)
    grade = _grade_case(case, result)
    _recorder.record(case, grade, result.stdout or "")
    assert grade.passed, (
        f"case {case.id} ({case.agent}) failed: {grade.reasons}"
    )


# ---------------------------------------------------------------------------
# Structural guards on the catalog itself
# ---------------------------------------------------------------------------


def test_cases_run_real_machinery() -> None:
    """No case may be answered by anything other than production code.

    This is the guard that replaces ``test_every_catalog_case_has_smoke_
    envelope``, and it inverts that test's premise. The old guard demanded
    that every case ship a hand-written stdout fixture; what it actually
    enforced was that every case be gradeable against a string the test
    suite wrote for itself -- which is why cases survived for months
    asserting things that had stopped being true of the system.

    Every case must therefore reach a backend that produces its answer from
    production code. ``routing_sim`` qualifies: it runs the real
    ``RoutingSimulator`` over the real routing table. The guard no longer
    carries a ``live_only`` escape hatch, because the lane it exempted --
    ``claude --print --agent <specialist>`` -- bypassed the orchestrator
    every real session is rooted in, and so measured a mode Gaia does not
    route through.
    """
    offenders = [c.id for c in _ALL_CASES if c.backend != "routing_sim"]
    assert not offenders, (
        f"cases {offenders} declare a backend this suite cannot dispatch. Use "
        "`routing_sim`, or move the property they measure into a unit test "
        "against the production function. Do not answer them with a canned "
        "fixture."
    )


def test_every_case_has_a_baseline_entry() -> None:
    """A case with no baseline entry is a case the drift gate cannot fail.

    ``compare_to_baseline`` records an unknown case as "new" and declines to
    flag it -- correct for a genuinely first run, and a permanent blind spot
    for a case that simply never got seeded. Requiring the entry up front
    keeps every case inside the gate from its first run onward.
    """
    baseline_ids = set(load_baseline(_BASELINE_PATH).get("cases", {}))
    missing = [c.id for c in _ALL_CASES if c.id not in baseline_ids]
    assert not missing, (
        f"cases {missing} have no entry in {_BASELINE_PATH}; seed one (with "
        "the score the case is expected to hold) so a later drop fails the run"
    )


def test_baseline_has_no_entries_for_deleted_cases() -> None:
    """The reverse: a baseline entry with no case is a stale gate.

    It never fires (nothing records that id) and it misleads the next reader
    into thinking the case still exists.
    """
    case_ids = {c.id for c in _ALL_CASES}
    stale = sorted(set(load_baseline(_BASELINE_PATH).get("cases", {})) - case_ids)
    assert not stale, (
        f"{_BASELINE_PATH} still carries entries for deleted case(s) {stale}; "
        "remove them when the case goes"
    )
