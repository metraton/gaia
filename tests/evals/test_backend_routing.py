"""Tests for :class:`tests.evals.runner.RoutingSimBackend` and
:func:`tests.evals.graders.routing_grader` (T3d).

Two layers of coverage:

1. **Unit layer (no config needed)**: Inject a stub simulator into
   ``RoutingSimBackend.simulator`` so the backend round-trip can be
   tested without reading ``config/surface-routing.json``. The stub
   also exercises the "non-dataclass result" fallback path.
2. **Integration layer (uses real gaia-ops config)**: Let the backend
   build a real ``RoutingSimulator`` from ``<repo>/config`` and
   ``<repo>/agents`` and assert the S4 (``kubectl apply``) prompt
   routes to ``gitops-operator`` or ``cloud-troubleshooter`` -- the
   routing expectation pinned in ``catalogs/context_consumption.yaml``.

The grader layer exercises every DSL branch: ``primary_agent`` exact
match, ``primary_agent_in``, ``primary_agent_not``, ``adjacent_*``,
``surfaces_contains``, ``min_confidence``, plus error paths (unknown
key, malformed JSON, non-object payload).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

from tests.evals.graders import GradeResult, routing_grader
from tests.evals.runner import (
    DispatchResult,
    EvalError,
    RoutingSimBackend,
    dispatch,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Stub simulator for unit-level tests
# ---------------------------------------------------------------------------


@dataclass
class _StubRoutingResult:
    """Shape-compatible stand-in for routing_simulator.RoutingResult."""

    prompt: str
    surfaces_active: list[str]
    primary_agent: str
    adjacent_agents: list[str]
    skills_loaded: list[str] = field(default_factory=list)
    context_sections: list[str] = field(default_factory=list)
    tokens_estimate: int = 0
    contracts: dict = field(default_factory=lambda: {"read": [], "write": []})
    confidence: float = 0.9
    multi_surface: bool = False


class _StubSimulator:
    """Minimal simulator returning canned :class:`_StubRoutingResult`."""

    def __init__(self, response: _StubRoutingResult) -> None:
        self.response = response
        self.calls: list[str] = []

    def simulate(self, prompt: str, agent_type: Optional[str] = None) -> _StubRoutingResult:
        self.calls.append(prompt)
        return self.response


# ---------------------------------------------------------------------------
# Backend: contract with DispatchBackend protocol
# ---------------------------------------------------------------------------


class TestRoutingSimBackendContract:
    """The backend must match the :class:`DispatchBackend` protocol shape."""

    def test_dispatch_returns_dispatch_result_with_json_stdout(self) -> None:
        stub = _StubSimulator(
            _StubRoutingResult(
                prompt="kubectl apply -f foo.yaml",
                surfaces_active=["gitops_desired_state"],
                primary_agent="gitops-operator",
                adjacent_agents=["cloud-troubleshooter"],
                confidence=0.95,
            )
        )
        backend = RoutingSimBackend(simulator=stub)

        result = backend.dispatch(
            agent_type="gaia-orchestrator",
            task="kubectl apply -f foo.yaml",
        )

        assert isinstance(result, DispatchResult)
        assert result.session_path is None
        assert result.audit_paths == []
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["primary_agent"] == "gitops-operator"
        assert payload["adjacent_agents"] == ["cloud-troubleshooter"]
        assert payload["surfaces_active"] == ["gitops_desired_state"]
        assert payload["confidence"] == 0.95
        # The simulator saw the catalog task, not the agent_type arg.
        assert stub.calls == ["kubectl apply -f foo.yaml"]

    def test_dispatch_via_public_dispatch_function(self) -> None:
        """The public ``dispatch()`` entry point routes through the backend."""
        stub = _StubSimulator(
            _StubRoutingResult(
                prompt="kubectl get pods",
                surfaces_active=["cloud_live_diagnostics"],
                primary_agent="cloud-troubleshooter",
                adjacent_agents=[],
            )
        )
        backend = RoutingSimBackend(simulator=stub)

        result = dispatch(
            agent_type="gaia-orchestrator",
            task="kubectl get pods",
            backend=backend,
        )

        payload = json.loads(result.stdout)
        assert payload["primary_agent"] == "cloud-troubleshooter"

    def test_invalid_agent_type_raises_eval_error(self) -> None:
        backend = RoutingSimBackend(simulator=_StubSimulator(
            _StubRoutingResult(
                prompt="x",
                surfaces_active=[],
                primary_agent="developer",
                adjacent_agents=[],
            )
        ))
        with pytest.raises(EvalError):
            backend.dispatch(agent_type="", task="x")

    def test_missing_config_dir_raises_eval_error(self, tmp_path: Path) -> None:
        """Real lazy init fails fast when config dir is absent.

        We reuse the real ``repo_root`` (so the simulator module imports)
        but override ``config_dir`` to a non-existent path. The check in
        ``_get_simulator`` must surface an :class:`EvalError` before the
        real simulator is constructed.
        """
        backend = RoutingSimBackend(
            repo_root=REPO_ROOT,
            config_dir=tmp_path / "does-not-exist",
        )
        with pytest.raises(EvalError) as excinfo:
            backend.dispatch(agent_type="gaia-orchestrator", task="kubectl apply")
        assert "config" in str(excinfo.value)

    def test_missing_agents_dir_raises_eval_error(self, tmp_path: Path) -> None:
        """Symmetric failure for a missing agents dir."""
        backend = RoutingSimBackend(
            repo_root=REPO_ROOT,
            agents_dir=tmp_path / "does-not-exist",
        )
        with pytest.raises(EvalError) as excinfo:
            backend.dispatch(agent_type="gaia-orchestrator", task="kubectl apply")
        assert "agents" in str(excinfo.value)

    def test_dict_result_fallback(self) -> None:
        """Accept a plain-dict shim for result objects that aren't dataclasses."""

        class _DictSimulator:
            def simulate(self, prompt: str, agent_type: Optional[str] = None) -> dict:
                return {
                    "primary_agent": "developer",
                    "adjacent_agents": [],
                    "surfaces_active": [],
                    "confidence": 0.5,
                    "multi_surface": False,
                    "prompt": prompt,
                }

        backend = RoutingSimBackend(simulator=_DictSimulator())
        result = backend.dispatch(agent_type="gaia-orchestrator", task="whatever")
        payload = json.loads(result.stdout)
        assert payload["primary_agent"] == "developer"


# ---------------------------------------------------------------------------
# Integration layer: use real RoutingSimulator with gaia-ops config
# ---------------------------------------------------------------------------


class TestRoutingSimBackendIntegration:
    """Exercise the real ``RoutingSimulator`` with gaia-ops ``config/``.

    These tests confirm that (a) the lazy-loading path wires up, and
    (b) the S4 routing expectation pinned in
    ``catalogs/context_consumption.yaml`` is satisfied by the actual
    ``surface-routing.json`` contents today.
    """

    def test_default_repo_root_resolves_to_gaia_ops(self) -> None:
        backend = RoutingSimBackend()
        assert (backend.repo_root / "config" / "surface-routing.json").is_file()
        assert (backend.repo_root / "agents").is_dir()

    def test_s4_kubectl_apply_deflects_from_orchestrator(self) -> None:
        """S4 routing_expect: primary in {gitops-operator, cloud-troubleshooter},
        and NOT gaia-orchestrator.
        """
        backend = RoutingSimBackend()
        result = backend.dispatch(
            agent_type="gaia-orchestrator",
            task="kubectl apply -f foo.yaml",
        )

        payload = json.loads(result.stdout)
        assert payload["primary_agent"] in {
            "gitops-operator",
            "cloud-troubleshooter",
        }, f"unexpected primary_agent: {payload['primary_agent']}"
        assert payload["primary_agent"] != "gaia-orchestrator"


# ---------------------------------------------------------------------------
# routing_grader: DSL branches
# ---------------------------------------------------------------------------


def _routing_payload(**overrides: Any) -> str:
    """Build a JSON-serialised RoutingResult-like payload for the grader."""
    payload = {
        "primary_agent": "gitops-operator",
        "adjacent_agents": ["cloud-troubleshooter"],
        "surfaces_active": ["gitops_desired_state"],
        "confidence": 0.9,
        "multi_surface": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestRoutingGraderHappyPaths:
    def test_empty_expect_passes(self) -> None:
        result = routing_grader(_routing_payload(), routing_expect={})
        assert isinstance(result, GradeResult)
        assert result.passed is True
        assert result.score == 1.0

    def test_primary_agent_exact_match(self) -> None:
        result = routing_grader(
            _routing_payload(primary_agent="gitops-operator"),
            routing_expect={"primary_agent": "gitops-operator"},
        )
        assert result.passed is True
        assert result.score == 1.0

    def test_primary_agent_in_allowed_set(self) -> None:
        """Mirrors S4: allowed set {gitops-operator, cloud-troubleshooter}."""
        result = routing_grader(
            _routing_payload(primary_agent="cloud-troubleshooter"),
            routing_expect={
                "primary_agent_in": ["gitops-operator", "cloud-troubleshooter"],
                "primary_agent_not": ["gaia-orchestrator"],
            },
        )
        assert result.passed is True

    def test_adjacent_contains_all_required(self) -> None:
        result = routing_grader(
            _routing_payload(adjacent_agents=["cloud-troubleshooter", "developer"]),
            routing_expect={"adjacent_contains": ["cloud-troubleshooter"]},
        )
        assert result.passed is True

    def test_surfaces_contains_all_required(self) -> None:
        result = routing_grader(
            _routing_payload(surfaces_active=["gitops_desired_state", "cloud_live"]),
            routing_expect={"surfaces_contains": ["gitops_desired_state"]},
        )
        assert result.passed is True

    def test_min_confidence_satisfied(self) -> None:
        result = routing_grader(
            _routing_payload(confidence=0.9),
            routing_expect={"min_confidence": 0.8},
        )
        assert result.passed is True


class TestRoutingGraderFailurePaths:
    def test_primary_agent_mismatch_fails(self) -> None:
        result = routing_grader(
            _routing_payload(primary_agent="developer"),
            routing_expect={"primary_agent": "gitops-operator"},
        )
        assert result.passed is False
        assert result.score == 0.0
        assert any("mismatch" in r for r in result.reasons)

    def test_primary_agent_in_rejects_outsider(self) -> None:
        result = routing_grader(
            _routing_payload(primary_agent="developer"),
            routing_expect={
                "primary_agent_in": ["gitops-operator", "cloud-troubleshooter"],
            },
        )
        assert result.passed is False

    def test_primary_agent_not_catches_forbidden(self) -> None:
        """S4 core assertion: must NOT stay with gaia-orchestrator."""
        result = routing_grader(
            _routing_payload(primary_agent="gaia-orchestrator"),
            routing_expect={"primary_agent_not": ["gaia-orchestrator"]},
        )
        assert result.passed is False
        assert any("forbidden" in r for r in result.reasons)

    def test_adjacent_contains_missing_entry_fails(self) -> None:
        result = routing_grader(
            _routing_payload(adjacent_agents=["developer"]),
            routing_expect={"adjacent_contains": ["cloud-troubleshooter"]},
        )
        assert result.passed is False

    def test_adjacent_not_leaked_fails(self) -> None:
        result = routing_grader(
            _routing_payload(adjacent_agents=["gaia-orchestrator"]),
            routing_expect={"adjacent_not": ["gaia-orchestrator"]},
        )
        assert result.passed is False

    def test_surfaces_contains_missing_fails(self) -> None:
        result = routing_grader(
            _routing_payload(surfaces_active=[]),
            routing_expect={"surfaces_contains": ["gitops_desired_state"]},
        )
        assert result.passed is False

    def test_min_confidence_below_threshold_fails(self) -> None:
        result = routing_grader(
            _routing_payload(confidence=0.4),
            routing_expect={"min_confidence": 0.8},
        )
        assert result.passed is False


class TestRoutingGraderErrorHandling:
    def test_unknown_expect_key_fails(self) -> None:
        result = routing_grader(
            _routing_payload(),
            routing_expect={"bogus_field": "x"},
        )
        assert result.passed is False
        assert any("unknown keys" in r for r in result.reasons)

    def test_malformed_json_response_fails(self) -> None:
        result = routing_grader(
            "this is not JSON at all",
            routing_expect={"primary_agent": "gitops-operator"},
        )
        assert result.passed is False
        assert any("not valid JSON" in r for r in result.reasons)

    def test_non_object_payload_fails(self) -> None:
        result = routing_grader(
            json.dumps(["list", "not", "object"]),
            routing_expect={"primary_agent": "gitops-operator"},
        )
        assert result.passed is False
        assert any("must be a JSON object" in r for r in result.reasons)

    def test_non_numeric_min_confidence_fails(self) -> None:
        result = routing_grader(
            _routing_payload(),
            routing_expect={"min_confidence": "high"},
        )
        assert result.passed is False
        assert any("must be numeric" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# End-to-end: backend.dispatch() -> routing_grader() on the live S4 prompt
# ---------------------------------------------------------------------------


class TestRoutingEndToEndS4:
    """Full pipeline: real simulator + real S4 routing_expect contract."""

    def test_s4_prompt_passes_catalog_routing_expect(self) -> None:
        backend = RoutingSimBackend()
        dispatch_result = backend.dispatch(
            agent_type="gaia-orchestrator",
            task="kubectl apply -f foo.yaml",
        )
        grade = routing_grader(
            dispatch_result.stdout,
            routing_expect={
                "primary_agent_in": ["gitops-operator", "cloud-troubleshooter"],
                "primary_agent_not": ["gaia-orchestrator"],
            },
        )
        assert grade.passed is True, grade.reasons
        assert grade.score == 1.0
