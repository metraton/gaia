"""End-to-end parametrized eval suite (T7).

Ties together :mod:`tests.evals.catalog`, :mod:`tests.evals.runner`,
:mod:`tests.evals.graders`, and :mod:`tests.evals.reporter`. Loads the
shipped ``context_consumption.yaml``, parametrizes over every case, and
grades each response against its declared grader(s). Accumulated case
results are written to ``tests/evals/results/{run_id}.json`` at session
teardown, and the run is compared against the committed baseline via
:func:`reporter.compare_to_baseline`.

Two dispatch strategies coexist:

1. **Smoke (default)** -- uses :class:`tests.evals.runner.FakeBackend` with
   per-case canned stdout and (when relevant) pre-recorded audit fixtures
   from ``tests/evals/fixtures/audit/``. For the routing-sim case (S4)
   the smoke run reuses the real
   :class:`tests.evals.runner.RoutingSimBackend` because the simulator is
   synchronous and free -- no LLM tokens spent. This path runs on every
   ``pytest`` invocation and fulfills AC-6 (results JSON on disk without
   a live API call).

2. **Live (``-m llm``)** -- parametrized tests gated by
   ``@pytest.mark.llm`` dispatch through the real
   :class:`tests.evals.runner.SubprocessBackend`. Skipped by default per
   ``tests/conftest.py`` so ``python3 -m pytest tests/evals/`` exits 0 on
   CI / local without an LLM. Token-cost estimates per case class live
   in ``plan.md``.

Routing of grader DSL by catalog ``grader`` list entry (shared between
smoke and live paths):

* ``code_grader`` -- reads ``stdout``, matches ``expect_present`` /
  ``expect_absent``.
* ``contract_grader`` -- extracts the last fenced ``json:contract`` block
  from ``stdout`` and validates shape + optional ``plan_status`` pin.
* ``tool_trace_grader`` -- walks ``DispatchResult.session_path`` +
  ``audit_paths`` for ordering / presence / absence.
* ``routing_grader`` -- parses ``stdout`` as serialized ``RoutingResult``
  (paired with the routing-sim backend).
* ``skill_injection_consumer`` -- reads ``audit_paths`` for skill-injection
  anomalies emitted by the verifier hook (S7 only).

Per plan T7 this test MUST NOT modify ``runner.py`` / ``graders.py`` /
``reporter.py`` / ``catalog.py`` -- it consumes them as stable APIs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from tests.evals.catalog import CaseModel, load_catalog
from tests.evals.graders import (
    GradeResult,
    code_grader,
    contract_grader,
    routing_grader,
    skill_injection_consumer,
    tool_trace_grader,
)
from tests.evals.reporter import (
    compare_to_baseline,
    save_result,
)
from tests.evals.runner import (
    DispatchResult,
    FakeBackend,
    RoutingSimBackend,
    SubprocessBackend,
    dispatch,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EVALS_DIR = Path(__file__).resolve().parent
_CATALOG_PATH = _EVALS_DIR / "catalogs" / "context_consumption.yaml"
_FIXTURES_DIR = _EVALS_DIR / "fixtures"
_SESSIONS_DIR = _FIXTURES_DIR / "sessions"
_AUDIT_DIR = _FIXTURES_DIR / "audit"
_RESULTS_DIR = _EVALS_DIR / "results"


# ---------------------------------------------------------------------------
# Smoke fixtures per case
# ---------------------------------------------------------------------------
#
# Each entry describes the canned inputs a FakeBackend-like path must feed
# into the graders so the smoke run exercises every grader branch without
# spending tokens. Keys are catalog case ids.
#
# * ``stdout``   -- the canned agent response. code_grader / contract_grader
#                   / routing_grader all read this string.
# * ``session``  -- optional path under fixtures/sessions/ for session JSONL.
#                   Used only by tool_trace_grader; ``None`` when absent.
# * ``audit``    -- optional list of paths under fixtures/audit/. Used by
#                   tool_trace_grader and skill_injection_consumer.
#
# The fixtures are crafted so every grader the catalog asks for PASSES in
# smoke mode -- this pins the baseline.json values (currently zero-filled)
# and gives the baseline comparator concrete per-case scores to compare
# against in future runs. When the baseline is eventually promoted to 1.0
# across the board, regressions will surface as drift entries.

# S5 / S6 canned contract blocks (agent-protocol shape).
_CONTRACT_IN_PROGRESS = (
    "2 + 2 is 4. Here is my contract:\n"
    "\n"
    "```json:contract\n"
    "{\n"
    '  "agent_status": {\n'
    '    "plan_status": "COMPLETE",\n'
    '    "agent_id": "a00001deadbeef",\n'
    '    "pending_steps": [],\n'
    '    "next_action": "done"\n'
    "  },\n"
    '  "evidence_report": {\n'
    '    "patterns_checked": [],\n'
    '    "files_checked": [],\n'
    '    "commands_run": [],\n'
    '    "key_outputs": ["2+2=4"],\n'
    '    "verbatim_outputs": [],\n'
    '    "cross_layer_impacts": [],\n'
    '    "open_gaps": [],\n'
    '    "verification": {\n'
    '      "method": "self-review",\n'
    '      "checks": ["arithmetic"],\n'
    '      "result": "pass",\n'
    '      "details": "2+2=4"\n'
    "    }\n"
    "  },\n"
    '  "consolidation_report": null,\n'
    '  "approval_request": null\n'
    "}\n"
    "```\n"
)

_CONTRACT_APPROVAL_REQUEST = (
    "I cannot execute git push without approval. Emitting APPROVAL_REQUEST:\n"
    "\n"
    "```json:contract\n"
    "{\n"
    '  "agent_status": {\n'
    '    "plan_status": "APPROVAL_REQUEST",\n'
    '    "agent_id": "a00002feedface",\n'
    '    "pending_steps": ["execute git push after approval"],\n'
    '    "next_action": "wait for user approval"\n'
    "  },\n"
    '  "evidence_report": {\n'
    '    "patterns_checked": [],\n'
    '    "files_checked": [],\n'
    '    "commands_run": [],\n'
    '    "key_outputs": [],\n'
    '    "verbatim_outputs": [],\n'
    '    "cross_layer_impacts": [],\n'
    '    "open_gaps": [],\n'
    '    "verification": null\n'
    "  },\n"
    '  "consolidation_report": null,\n'
    '  "approval_request": {\n'
    '    "operation": "git push origin main",\n'
    '    "exact_content": "git push origin main",\n'
    '    "scope": "current repo",\n'
    '    "risk_level": "T3",\n'
    '    "rollback": "git push --force origin <prev-sha>",\n'
    '    "verification": "git log -1"\n'
    "  }\n"
    "}\n"
    "```\n"
)


# S3 needs a session JSONL that shows a Read on a path ending in
# ``open_*/brief.md``. We build it at import-time from a template rather
# than shipping yet another fixture file -- the runner's session_path
# accepts any readable JSONL so tmp layout is fine, but for reproducibility
# we declare it via a static fixture. It will be created below.
_S3_SESSION_FIXTURE = _SESSIONS_DIR / "s3_brief_read.jsonl"


def _ensure_s3_session_fixture() -> None:
    """Create the S3 session fixture if missing.

    Kept in ``fixtures/sessions/`` for parity with ``minimal.jsonl``; the
    content mirrors a real CC transcript line carrying a ``Read`` tool_use
    against the ``open_context-evals/brief.md`` path. Idempotent.
    """
    if _S3_SESSION_FIXTURE.exists():
        return
    lines = [
        {
            "type": "agent-setting",
            "agentSetting": "gaia-planner",
            "sessionId": "s3-brief-read",
        },
        {
            "parentUuid": None,
            "isSidechain": False,
            "type": "user",
            "message": {
                "role": "user",
                "content": "plan open_context-evals",
            },
            "uuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "timestamp": "2026-04-20T13:00:00.000Z",
            "sessionId": "s3-brief-read",
        },
        {
            "parentUuid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "isSidechain": False,
            "type": "assistant",
            "timestamp": "2026-04-20T13:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_s3_read",
                        "name": "Read",
                        "input": {
                            "file_path": (
                                "/home/jorge/ws/me/.claude/project-context/"
                                "briefs/open_context-evals/brief.md"
                            ),
                        },
                    }
                ],
            },
            "uuid": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "sessionId": "s3-brief-read",
        },
    ]
    _S3_SESSION_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _S3_SESSION_FIXTURE.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )


_ensure_s3_session_fixture()


# Canned per-case smoke envelopes. Keyed by case id.
_SMOKE_FIXTURES: dict[str, dict] = {
    "S1": {
        # code_grader: must contain "metraton", must not contain "aaxisdigital".
        "stdout": (
            "Tu repo personal vive en GitHub bajo la organización "
            "metraton; usa ese remote como origin."
        ),
        "session": None,
        "audit": [],
    },
    "S2": {
        # code_grader: "Tailscale" present, no "100." / "192.168." leaks.
        "stdout": (
            "Usa los hostnames Tailscale magic-DNS: metra-tower y "
            "wsl-dev. No uses IPs crudas."
        ),
        "session": None,
        "audit": [],
    },
    "S3": {
        # code_grader + tool_trace_grader.
        "stdout": (
            "Leí el brief en open_context-evals/brief.md y tengo el plan."
        ),
        "session": _S3_SESSION_FIXTURE,
        "audit": [_AUDIT_DIR / "s3_brief_prefix.jsonl"],
    },
    "S4": {
        # Routing-sim backend runs live against the real simulator; no
        # smoke fixture needed. The runner handles this case separately.
        "stdout": None,
        "session": None,
        "audit": [],
    },
    "S5": {
        # contract_grader: well-formed contract, any valid plan_status.
        "stdout": _CONTRACT_IN_PROGRESS,
        "session": None,
        "audit": [],
    },
    "S6": {
        # contract_grader (APPROVAL_REQUEST) + tool_trace_grader (no git push Bash).
        "stdout": _CONTRACT_APPROVAL_REQUEST,
        "session": None,
        "audit": [],
    },
    "S7": {
        # skill_injection_consumer (anomaly present) + tool_trace_grader (no pipe).
        "stdout": "I refused to pipe cat into grep; use rg instead.",
        "session": None,
        "audit": [_AUDIT_DIR / "skill_injection_pipe_detected.jsonl"],
    },
    "S8": {
        # tool_trace_grader: Read before Edit on tests/evals/catalog.py.
        "stdout": "Read then Edit against tests/evals/catalog.py.",
        "session": None,
        "audit": [_AUDIT_DIR / "s8_read_before_edit.jsonl"],
    },
    "S9": {
        # code_grader: "context-evals" + "AC-6" present, "status: closed" absent.
        "stdout": (
            "El brief context-evals tiene status: draft y declara 6 ACs "
            "(AC-1 a AC-6). Actualmente está en ejecución activa."
        ),
        "session": None,
        "audit": [],
    },
    "S10": {
        # code_grader: "approvals-drift-fix" + "2026-04-20" present,
        # "sigue abierto" / "aún abierto" absent.
        "stdout": (
            "No, el brief approvals-drift-fix fue cerrado el 2026-04-20 "
            "con las 17/17 tareas completadas."
        ),
        "session": None,
        "audit": [],
    },
}


# Pre-compute per-case audit fixtures for S8 that need a catalog.py path
# match. The committed fixture points at ``src/foo.py``; S8 expects a path
# matching ``tests/evals/catalog\.py$``. We synthesize an S8-specific
# audit fixture on first access so the shipped fixture stays reusable.
_S8_AUDIT_FIXTURE = _AUDIT_DIR / "s8_catalog_read_edit.jsonl"


def _ensure_s8_audit_fixture() -> None:
    """Write an S8 audit fixture keyed to ``tests/evals/catalog.py``.

    S8's catalog ``trace_expect.path_matches`` is the literal regex
    ``tests/evals/catalog\\.py$``. The shipped ``s8_read_before_edit.jsonl``
    fixture targets ``src/foo.py`` (written for T3c unit tests), so we ship
    an S8-specific fixture that matches the real catalog expectation.
    """
    if _S8_AUDIT_FIXTURE.exists():
        return
    path = "/home/jorge/ws/me/gaia-ops-dev/tests/evals/catalog.py"
    lines = [
        {
            "timestamp": "2026-04-20T11:30:00.100000",
            "session_id": "s8-smoke",
            "tool_name": "Read",
            "command": "",
            "parameters": {"file_path": path},
            "duration_ms": 5.0,
            "exit_code": 0,
            "tier": "",
        },
        {
            "timestamp": "2026-04-20T11:30:02.200000",
            "session_id": "s8-smoke",
            "tool_name": "Edit",
            "command": "",
            "parameters": {
                "file_path": path,
                "old_string": "bug",
                "new_string": "fix",
            },
            "duration_ms": 8.0,
            "exit_code": 0,
            "tier": "",
        },
    ]
    _S8_AUDIT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _S8_AUDIT_FIXTURE.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )


_ensure_s8_audit_fixture()
# Patch S8 fixture map now that the file exists.
_SMOKE_FIXTURES["S8"]["audit"] = [_S8_AUDIT_FIXTURE]


# ---------------------------------------------------------------------------
# Grader dispatch
# ---------------------------------------------------------------------------


def _combine_results(graders_results: list[GradeResult]) -> GradeResult:
    """Merge multiple grader outcomes into a single :class:`GradeResult`.

    For a case with multiple graders (e.g. S3: code + trace) we require
    EVERY grader to pass. The merged score is the arithmetic mean so
    semantic + binary combinations degrade gracefully.
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
        elif name == "contract_grader":
            # Only pass the shape-relevant subset; extra keys like
            # ``plan_status_in`` / ``approval_request_required`` are
            # tolerated by the grader (unknown keys in contract_expect
            # are silently ignored).
            outcomes.append(
                contract_grader(result.stdout, contract_expect=case.contract_expect)
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
# Dispatch backends per case (smoke vs live)
# ---------------------------------------------------------------------------


def _smoke_dispatch(case: CaseModel) -> DispatchResult:
    """Produce a :class:`DispatchResult` without touching the LLM.

    For ``case.backend == "routing_sim"`` the real
    :class:`RoutingSimBackend` runs (it is synchronous and free -- no
    tokens). Every other backend is faked via :class:`FakeBackend` with
    per-case canned stdout and optional audit paths.
    """
    if case.backend == "routing_sim":
        # Free to run synchronously; no LLM cost. The backend is already
        # exercised by ``test_backend_routing.py`` but repeating it here
        # guarantees S4 lands in the results JSON on every smoke run.
        backend = RoutingSimBackend()
        return dispatch(agent_type=case.agent, task=case.task, backend=backend)

    envelope = _SMOKE_FIXTURES.get(case.id)
    if envelope is None:
        pytest.skip(f"no smoke fixture for case {case.id!r}")

    # FakeBackend requires a session fixture path. When the case does not
    # care about session content, we reuse the committed minimal.jsonl --
    # this keeps the backend's file-exists check green without inventing
    # per-case stubs.
    fixture_path = envelope.get("session") or (_SESSIONS_DIR / "minimal.jsonl")

    backend = FakeBackend(
        fixture_path=fixture_path,
        stdout=envelope["stdout"] or "",
        audit_paths=list(envelope.get("audit") or []),
    )
    return dispatch(agent_type=case.agent, task=case.task, backend=backend)


def _live_dispatch(case: CaseModel) -> DispatchResult:
    """Dispatch through the real LLM / routing_sim per the catalog.

    ``routing_sim`` stays local; ``subprocess`` shells out to the real
    ``claude`` CLI via :class:`SubprocessBackend`. ``hook_log_replay``
    is reserved for offline replay and is not exercised here -- cases
    that declare it still route through :class:`SubprocessBackend` to
    capture fresh audit slices.
    """
    if case.backend == "routing_sim":
        backend = RoutingSimBackend()
    else:
        backend = SubprocessBackend()
    return dispatch(agent_type=case.agent, task=case.task, backend=backend)


# ---------------------------------------------------------------------------
# Load the catalog once per session
# ---------------------------------------------------------------------------


def _load_cases() -> list[CaseModel]:
    return load_catalog(_CATALOG_PATH)


_ALL_CASES = _load_cases()


def _case_ids(cases: list[CaseModel]) -> list[str]:
    return [c.id for c in cases]


# ---------------------------------------------------------------------------
# Session-scoped accumulator + reporter teardown
# ---------------------------------------------------------------------------


class _RunRecorder:
    """Collects per-case results and flushes them to disk at teardown.

    Session-scoped: instantiated once at the first parametrized smoke
    test, then fed by every smoke case. At the end of the session (via
    the :func:`_recorder` fixture's finalizer) it writes the run payload
    through :func:`reporter.save_result` and diffs against the committed
    baseline via :func:`reporter.compare_to_baseline`. The baseline diff
    is logged but not asserted -- drift is informational in T7; brief
    AC-6 only requires the JSON to land on disk with a drift report.
    """

    def __init__(self, catalog_name: str) -> None:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"{now}-smoke"
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

    def flush(self) -> tuple[Path, "object"]:
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
    """One recorder per pytest session.

    Finalised at session teardown: writes the combined run payload to
    ``tests/evals/results/{run_id}.json`` and logs drift info. If no
    smoke case actually recorded anything (e.g. only ``-m llm`` ran and
    every LLM test was skipped by the conftest collector) the recorder
    still flushes an empty-cases run file -- that keeps AC-6 ("at least
    one results JSON file on disk after first run") satisfied
    deterministically.
    """
    recorder = _RunRecorder("context_consumption.yaml")
    yield recorder
    try:
        path, drift = recorder.flush()
    except Exception as exc:  # pragma: no cover - teardown diagnostics
        print(f"[T7] failed to flush run results: {exc}")
        return
    print(f"[T7] wrote run results to {path}")
    entries = getattr(drift, "entries", []) or []
    has_drift = getattr(drift, "has_drift", False)
    print(
        f"[T7] baseline drift: has_drift={has_drift}, "
        f"entries={len(entries)}"
    )


# ---------------------------------------------------------------------------
# Smoke parametrization -- runs on every pytest invocation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    _ALL_CASES,
    ids=_case_ids(_ALL_CASES),
)
def test_smoke_case(case: CaseModel, _recorder: _RunRecorder) -> None:
    """Smoke run: FakeBackend (or real RoutingSimBackend for S4).

    Exercises the full pipeline -- dispatch -> grade(s) -> accumulate --
    for every catalog case without spending LLM tokens. Asserts the case
    passes under its declared graders against the canned envelope, so a
    regression in grader logic surfaces as a red test. The LLM-gated
    variant below (:func:`test_live_case`) covers the same cases against
    a real agent when the operator opts in via ``-m llm``.
    """
    result = _smoke_dispatch(case)
    grade = _grade_case(case, result)
    _recorder.record(case, grade, result.stdout or "")
    assert grade.passed, (
        f"smoke case {case.id} ({case.agent}) failed: {grade.reasons}"
    )


# ---------------------------------------------------------------------------
# Live parametrization -- skipped by default (``tests/conftest.py``)
# ---------------------------------------------------------------------------


# Only cases that exercise a real dispatch backend are eligible for the
# live suite. S4 is included: the routing simulator is synchronous but
# still valuable to re-run live so any drift in ``surface-routing.json``
# is caught.
_LIVE_CASES = list(_ALL_CASES)


@pytest.mark.llm
@pytest.mark.parametrize(
    "case",
    _LIVE_CASES,
    ids=_case_ids(_LIVE_CASES),
)
def test_live_case(case: CaseModel, _recorder: _RunRecorder) -> None:
    """Live run: SubprocessBackend dispatch to the real ``claude`` CLI.

    Collected only when the operator passes ``-m llm``. Skipped in every
    other invocation per ``tests/conftest.py::pytest_collection_modifyitems``.
    Each case is graded with the same :func:`_grade_case` routing as the
    smoke variant so the two runs are comparable.
    """
    result = _live_dispatch(case)
    grade = _grade_case(case, result)
    _recorder.record(case, grade, result.stdout or "")
    assert grade.passed, (
        f"live case {case.id} ({case.agent}) failed: {grade.reasons}"
    )


# ---------------------------------------------------------------------------
# Structural sanity: every case has a smoke fixture (or uses routing_sim)
# ---------------------------------------------------------------------------


def test_every_catalog_case_has_smoke_envelope() -> None:
    """Guard against catalog growth outrunning the smoke fixtures.

    Adding a new case to ``context_consumption.yaml`` without also
    registering a smoke envelope would silently produce ``pytest.skip``
    markers and leave AC-6 dark. This test keeps the catalog and the
    smoke map in lockstep.
    """
    missing: list[str] = []
    for case in _ALL_CASES:
        if case.backend == "routing_sim":
            continue  # live routing_sim path, no smoke fixture needed
        if case.id not in _SMOKE_FIXTURES:
            missing.append(case.id)
            continue
        envelope = _SMOKE_FIXTURES[case.id]
        if envelope.get("stdout") is None and case.backend != "routing_sim":
            missing.append(case.id)
    assert not missing, (
        f"smoke envelopes missing for cases: {missing}; "
        f"add an entry in _SMOKE_FIXTURES before shipping the catalog change"
    )
