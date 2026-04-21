"""Tests for :mod:`tests.evals.catalog`.

Coverage:

- ``load_catalog`` on the shipped ``context_consumption.yaml``: succeeds,
  returns 10 cases, every case has required fields, every ``grader``
  entry is valid, every ``backend`` entry is valid.
- ``load_catalog`` error paths: missing file, invalid YAML, missing
  top-level keys, unknown grader / backend / scoring, duplicate case id.
- ``CaseModel`` default factories produce independent lists/dicts
  (``frozen=True`` dataclass sanity).

The tests are pure-Python -- no subprocess, no API, no fixtures beyond
``tmp_path``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.evals.catalog import (
    CaseModel,
    CatalogError,
    REQUIRED_CASE_KEYS,
    VALID_BACKENDS,
    VALID_GRADERS,
    VALID_SCORING,
    load_catalog,
)


# --------------------------------------------------------------------
# Shipped catalog: tests/evals/catalogs/context_consumption.yaml
# --------------------------------------------------------------------

SHIPPED_CATALOG = (
    Path(__file__).resolve().parent / "catalogs" / "context_consumption.yaml"
)


class TestShippedCatalog:
    """Smoke tests against the real ``context_consumption.yaml``."""

    def test_loads_successfully(self):
        cases = load_catalog(SHIPPED_CATALOG)
        assert isinstance(cases, list)
        assert all(isinstance(c, CaseModel) for c in cases)

    def test_has_exactly_ten_cases(self):
        cases = load_catalog(SHIPPED_CATALOG)
        assert len(cases) == 10

    def test_covers_all_ten_scenario_ids(self):
        cases = load_catalog(SHIPPED_CATALOG)
        ids = {c.id for c in cases}
        expected = {f"S{i}" for i in range(1, 11)}
        assert ids == expected

    def test_every_case_has_required_fields(self):
        cases = load_catalog(SHIPPED_CATALOG)
        for case in cases:
            assert case.id, f"case missing id"
            assert case.agent, f"{case.id}: empty agent"
            assert case.task, f"{case.id}: empty task"
            assert case.grader, f"{case.id}: empty grader list"
            assert case.backend, f"{case.id}: empty backend"
            assert case.scoring, f"{case.id}: empty scoring"

    def test_every_grader_is_valid(self):
        cases = load_catalog(SHIPPED_CATALOG)
        for case in cases:
            for grader in case.grader:
                assert grader in VALID_GRADERS, (
                    f"{case.id}: unknown grader {grader!r}"
                )

    def test_every_backend_is_valid(self):
        cases = load_catalog(SHIPPED_CATALOG)
        for case in cases:
            assert case.backend in VALID_BACKENDS, (
                f"{case.id}: invalid backend {case.backend!r}"
            )

    def test_every_scoring_is_valid(self):
        cases = load_catalog(SHIPPED_CATALOG)
        for case in cases:
            assert case.scoring in VALID_SCORING, (
                f"{case.id}: invalid scoring {case.scoring!r}"
            )

    def test_routing_sim_case_has_routing_expect(self):
        """S4 is the only routing_sim case; it must declare routing_expect."""
        cases = {c.id: c for c in load_catalog(SHIPPED_CATALOG)}
        s4 = cases["S4"]
        assert s4.backend == "routing_sim"
        assert s4.routing_expect, "S4 must declare routing_expect"

    def test_approval_case_expects_approval_request(self):
        """S6 is the T3 approval flow -- must expect APPROVAL_REQUEST."""
        cases = {c.id: c for c in load_catalog(SHIPPED_CATALOG)}
        s6 = cases["S6"]
        assert s6.contract_expect.get("plan_status") == "APPROVAL_REQUEST"

    def test_subject_archetype_coverage(self):
        """All 5 archetypes from the plan are exercised."""
        cases = load_catalog(SHIPPED_CATALOG)
        agents = {c.agent for c in cases}
        for archetype in (
            "gaia-orchestrator",
            "developer",
            "gaia-planner",
            "cloud-troubleshooter",
            "gaia-system",
        ):
            assert archetype in agents, (
                f"archetype {archetype} not covered by any case"
            )


# --------------------------------------------------------------------
# Synthetic YAMLs for loader behavior.
# --------------------------------------------------------------------


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


MINIMAL_VALID = """\
name: test_catalog
description: minimal valid catalog
version: 1
cases:
  - id: T1
    agent: developer
    task: "say hi"
    grader:
      - code_grader
    backend: subprocess
    scoring: binary
    expect_present:
      - hi
"""


class TestLoaderSuccess:
    def test_minimal_catalog_loads(self, tmp_path):
        cat = _write(tmp_path / "cat.yaml", MINIMAL_VALID)
        cases = load_catalog(cat)
        assert len(cases) == 1
        assert cases[0].id == "T1"
        assert cases[0].expect_present == ["hi"]
        assert cases[0].threshold == 0.8  # default

    def test_threshold_override(self, tmp_path):
        body = MINIMAL_VALID + "    threshold: 0.5\n"
        cat = _write(tmp_path / "cat.yaml", body)
        cases = load_catalog(cat)
        assert cases[0].threshold == 0.5

    def test_optional_dicts_default_to_empty(self, tmp_path):
        cat = _write(tmp_path / "cat.yaml", MINIMAL_VALID)
        cases = load_catalog(cat)
        case = cases[0]
        assert case.contract_expect == {}
        assert case.trace_expect == {}
        assert case.routing_expect == {}
        assert case.anomaly_expect == {}
        assert case.expect_absent == []


class TestLoaderFailure:
    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_catalog(tmp_path / "nope.yaml")

    def test_invalid_yaml(self, tmp_path):
        cat = _write(tmp_path / "bad.yaml", "name: [unclosed\n")
        with pytest.raises(CatalogError, match="invalid YAML"):
            load_catalog(cat)

    def test_missing_top_level_keys(self, tmp_path):
        cat = _write(tmp_path / "bad.yaml", "name: partial\n")
        with pytest.raises(CatalogError, match="missing required keys"):
            load_catalog(cat)

    def test_empty_cases_list(self, tmp_path):
        body = """\
        name: empty
        description: no cases
        version: 1
        cases: []
        """
        cat = _write(tmp_path / "bad.yaml", body)
        with pytest.raises(CatalogError, match="non-empty list"):
            load_catalog(cat)

    def test_unknown_grader(self, tmp_path):
        body = MINIMAL_VALID.replace("code_grader", "nonexistent_grader")
        cat = _write(tmp_path / "bad.yaml", body)
        with pytest.raises(CatalogError, match="unknown graders"):
            load_catalog(cat)

    def test_invalid_backend(self, tmp_path):
        body = MINIMAL_VALID.replace("backend: subprocess", "backend: ssh_dispatch")
        cat = _write(tmp_path / "bad.yaml", body)
        with pytest.raises(CatalogError, match="invalid backend"):
            load_catalog(cat)

    def test_invalid_scoring(self, tmp_path):
        body = MINIMAL_VALID.replace("scoring: binary", "scoring: ternary")
        cat = _write(tmp_path / "bad.yaml", body)
        with pytest.raises(CatalogError, match="invalid scoring"):
            load_catalog(cat)

    def test_missing_case_key(self, tmp_path):
        body = """\
        name: test
        description: missing task
        version: 1
        cases:
          - id: T1
            agent: developer
            grader: [code_grader]
            backend: subprocess
            scoring: binary
        """
        cat = _write(tmp_path / "bad.yaml", body)
        with pytest.raises(CatalogError, match="missing keys"):
            load_catalog(cat)

    def test_duplicate_case_id(self, tmp_path):
        body = """\
        name: dup
        description: duplicate ids
        version: 1
        cases:
          - id: T1
            agent: developer
            task: first
            grader: [code_grader]
            backend: subprocess
            scoring: binary
          - id: T1
            agent: developer
            task: second
            grader: [code_grader]
            backend: subprocess
            scoring: binary
        """
        cat = _write(tmp_path / "bad.yaml", body)
        with pytest.raises(CatalogError, match="duplicate case id"):
            load_catalog(cat)


class TestCaseModel:
    """Direct sanity on the dataclass defaults."""

    def test_default_lists_are_independent(self):
        a = CaseModel(
            id="A",
            agent="developer",
            task="t",
            grader=["code_grader"],
            backend="subprocess",
            scoring="binary",
        )
        b = CaseModel(
            id="B",
            agent="developer",
            task="t",
            grader=["code_grader"],
            backend="subprocess",
            scoring="binary",
        )
        # Dataclass default_factory must not share state between instances.
        assert a.expect_present is not b.expect_present
        assert a.contract_expect is not b.contract_expect

    def test_required_case_keys_matches_dataclass(self):
        """Contract sanity: the REQUIRED_CASE_KEYS list must match the
        dataclass fields that have no default value."""
        # id/agent/task/grader/backend/scoring have no defaults.
        assert set(REQUIRED_CASE_KEYS) == {
            "id",
            "agent",
            "task",
            "grader",
            "backend",
            "scoring",
        }
