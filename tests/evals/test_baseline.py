"""Tests for the T6 baseline snapshot + drift reporter.

T6 owns three public entry points in :mod:`tests.evals.reporter`:

* :func:`load_baseline` -- reads the baseline JSON, returns an empty
  skeleton when absent.
* :func:`compare_to_baseline` -- produces a :class:`DriftReport` with one
  :class:`DriftEntry` per case; semantic drift at ``abs(delta) > 0.10``,
  binary drift on exact mismatch.
* :func:`write_baseline_candidate` -- writes a promotion candidate to
  ``baseline.candidate.json`` (never overwrites the live baseline).

All tests use ``tmp_path`` -- no writes to the real
``tests/evals/results/`` directory, with one read-only exception that
validates the seeded ``baseline.json`` under version control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.reporter import (
    BaselineRegression,
    DriftEntry,
    DriftReport,
    compare_to_baseline,
    enforce_no_regression,
    load_baseline,
    write_baseline_candidate,
)


# --- Helpers ---------------------------------------------------------------


def _baseline_payload(
    cases: dict[str, dict] | None = None,
    version: int = 1,
) -> dict:
    """Produce a baseline payload dict in the canonical schema."""
    return {
        "version": version,
        "cases": cases or {},
    }


def _run_payload(cases: list[dict]) -> dict:
    """Produce a new-run payload shaped like T3a/T7."""
    return {
        "run_id": "20260420T1200Z-smoke",
        "catalog": "context_consumption.yaml",
        "cases": cases,
    }


def _write_baseline(dir_: Path, payload: dict) -> Path:
    path = dir_ / "baseline.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


# --- load_baseline ---------------------------------------------------------


class TestLoadBaseline:
    """Loading returns schema-shaped dicts; missing file is tolerated."""

    def test_returns_empty_skeleton_when_missing(self, tmp_path: Path):
        missing = tmp_path / "does-not-exist.json"
        data = load_baseline(missing)
        assert data == {"version": 1, "cases": {}}

    def test_parses_existing_file(self, tmp_path: Path):
        payload = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, payload)
        data = load_baseline(path)
        assert data["cases"]["S1"]["score"] == 0.9
        assert data["version"] == 1

    def test_defensive_against_missing_keys(self, tmp_path: Path):
        # Hand-edited baseline that dropped ``version`` / ``cases``.
        path = tmp_path / "baseline.json"
        path.write_text("{}\n")
        data = load_baseline(path)
        assert data["cases"] == {}
        assert data["version"] == 1

    def test_seeded_baseline_records_the_expected_passing_score(self):
        """The committed baseline holds each case's EXPECTED score, not zero.

        It used to be zero-filled for all ten case ids, which quietly
        neutered the whole mechanism: every real run scored 1.0, so every
        case reported drift on every run, and a report that is always
        alarming is a report nobody reads. A baseline is only a baseline if
        it records the value the case is supposed to hold -- then, and only
        then, does a departure from it mean something.

        Case-id membership is asserted against the catalog by
        ``test_evals.py`` (both directions); here we only check the shape and
        the values.
        """
        seeded = (
            Path(__file__).resolve().parent / "results" / "baseline.json"
        )
        assert seeded.exists()
        data = load_baseline(seeded)
        assert data["cases"], "baseline carries no cases"
        for case_id, entry in data["cases"].items():
            assert entry["score"] == 1.0, (
                f"{case_id}: baseline score should be the score the case is "
                f"expected to hold (1.0 = passing), got {entry['score']}"
            )
            assert entry["scoring"] in {"semantic", "binary"}, case_id


# --- compare_to_baseline ---------------------------------------------------


class TestCompareToBaselineNoDrift:
    """New scores within the threshold do not flag drift."""

    def test_no_drift_when_scores_equal(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [{"id": "S1", "score": 0.9, "scoring": "semantic"}]
        )

        report = compare_to_baseline(new, baseline_path=path)
        assert isinstance(report, DriftReport)
        assert report.has_drift is False
        assert report.missing_baseline is False
        assert len(report.entries) == 1
        entry = report.entries[0]
        assert isinstance(entry, DriftEntry)
        assert entry.drift is False
        assert entry.delta == pytest.approx(0.0)

    def test_no_drift_when_delta_exactly_at_threshold(self, tmp_path: Path):
        # Policy is ``> threshold``; equal to threshold is NOT drift.
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [{"id": "S1", "score": 0.8, "scoring": "semantic"}]
        )

        report = compare_to_baseline(new, baseline_path=path)
        assert report.has_drift is False
        assert report.entries[0].delta == pytest.approx(0.1)

    def test_binary_exact_match_no_drift(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S5": {"score": 1.0, "scoring": "binary"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [{"id": "S5", "score": 1.0, "scoring": "binary"}]
        )
        report = compare_to_baseline(new, baseline_path=path)
        assert report.has_drift is False
        assert report.entries[0].drift is False


class TestCompareToBaselineDriftFlagged:
    """New scores beyond the threshold flag drift."""

    def test_semantic_drift_above_threshold(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [{"id": "S1", "score": 0.5, "scoring": "semantic"}]
        )
        report = compare_to_baseline(new, baseline_path=path)
        assert report.has_drift is True
        entry = report.entries[0]
        assert entry.drift is True
        assert entry.delta == pytest.approx(0.4)
        assert "drift" in entry.reason.lower()

    def test_binary_regression_flags_drift(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S5": {"score": 1.0, "scoring": "binary"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [{"id": "S5", "score": 0.0, "scoring": "binary"}]
        )
        report = compare_to_baseline(new, baseline_path=path)
        assert report.has_drift is True
        assert report.entries[0].drift is True
        assert "regression" in report.entries[0].reason.lower()

    def test_mixed_run_flags_only_drifting_cases(self, tmp_path: Path):
        baseline = _baseline_payload(
            {
                "S1": {"score": 0.9, "scoring": "semantic"},
                "S5": {"score": 1.0, "scoring": "binary"},
            }
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [
                {"id": "S1", "score": 0.95, "scoring": "semantic"},  # no drift
                {"id": "S5", "score": 0.0, "scoring": "binary"},     # drift
            ]
        )
        report = compare_to_baseline(new, baseline_path=path)
        assert report.has_drift is True
        by_id = {e.id: e for e in report.entries}
        assert by_id["S1"].drift is False
        assert by_id["S5"].drift is True

    def test_custom_threshold_honoured(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [{"id": "S1", "score": 0.85, "scoring": "semantic"}]
        )
        # Default threshold 0.10 -> no drift; custom 0.01 -> drift.
        loose = compare_to_baseline(new, baseline_path=path, threshold=0.10)
        assert loose.has_drift is False
        strict = compare_to_baseline(new, baseline_path=path, threshold=0.01)
        assert strict.has_drift is True
        assert strict.threshold == 0.01


class TestCompareToBaselineMissingBaseline:
    """Missing baseline -> treat all as new, no drift flagged."""

    def test_missing_baseline_file_returns_no_drift(self, tmp_path: Path):
        missing = tmp_path / "nope.json"
        new = _run_payload(
            [
                {"id": "S1", "score": 0.75, "scoring": "semantic"},
                {"id": "S5", "score": 1.0, "scoring": "binary"},
            ]
        )
        report = compare_to_baseline(new, baseline_path=missing)
        assert report.missing_baseline is True
        assert report.has_drift is False
        assert len(report.entries) == 2
        for entry in report.entries:
            assert entry.drift is False
            assert entry.baseline_score is None
            assert "new case" in entry.reason

    def test_case_absent_from_baseline_is_new(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, baseline)
        new = _run_payload(
            [
                {"id": "S1", "score": 0.9, "scoring": "semantic"},
                {"id": "S99", "score": 0.3, "scoring": "semantic"},  # new
            ]
        )
        report = compare_to_baseline(new, baseline_path=path)
        assert report.has_drift is False
        by_id = {e.id: e for e in report.entries}
        assert by_id["S99"].baseline_score is None
        assert by_id["S99"].drift is False


class TestCompareToBaselineInputShapes:
    """``new_results`` accepts full payload, raw case list, or dataclass."""

    def test_accepts_case_list_directly(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        path = _write_baseline(tmp_path, baseline)
        cases = [{"id": "S1", "score": 0.9, "scoring": "semantic"}]
        report = compare_to_baseline(cases, baseline_path=path)
        assert report.has_drift is False
        assert len(report.entries) == 1

    def test_uses_default_baseline_path_when_none(self, tmp_path: Path):
        # When ``baseline_path`` is None we point at the real results dir, so
        # the case id has to be one the COMMITTED baseline actually carries --
        # S4, whose entry is the passing score 1.0. A run that scored 0.0 is a
        # full-drop regression against it. Any other id would be scored as a
        # new case and this test would assert nothing.
        new = _run_payload(
            [{"id": "S4", "score": 0.0, "scoring": "binary"}]
        )
        report = compare_to_baseline(new)
        assert report.missing_baseline is False
        assert report.has_drift is True
        assert report.has_regression is True

    def test_scoring_inferred_from_baseline_when_absent(self, tmp_path: Path):
        baseline = _baseline_payload(
            {"S5": {"score": 1.0, "scoring": "binary"}}
        )
        path = _write_baseline(tmp_path, baseline)
        # New run does not pass ``scoring``; grader must infer from baseline.
        new = _run_payload([{"id": "S5", "score": 0.0}])
        report = compare_to_baseline(new, baseline_path=path)
        assert report.entries[0].scoring == "binary"
        assert report.entries[0].drift is True


# --- direction: regression vs improvement ----------------------------------


class TestDriftDirection:
    """Drift is direction-agnostic; regression is the downward half.

    The distinction is what lets the gate below fail on bad news without
    firing on good news -- a gate that goes red when an agent IMPROVES is a
    gate that gets switched off, and a switched-off gate protects nothing.
    """

    def test_downward_semantic_drift_is_a_regression(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S1": {"score": 1.0, "scoring": "semantic"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S1", "score": 0.5, "scoring": "semantic"}]),
            baseline_path=path,
        )
        assert report.has_drift is True
        assert report.has_regression is True
        assert [e.id for e in report.regressions()] == ["S1"]
        assert "REGRESSION" in report.entries[0].reason

    def test_upward_semantic_drift_is_not_a_regression(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S1": {"score": 0.5, "scoring": "semantic"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S1", "score": 1.0, "scoring": "semantic"}]),
            baseline_path=path,
        )
        assert report.has_drift is True
        assert report.has_regression is False
        assert "improvement" in report.entries[0].reason

    def test_binary_flip_down_is_a_regression(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S6": {"score": 1.0, "scoring": "binary"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S6", "score": 0.0, "scoring": "binary"}]),
            baseline_path=path,
        )
        assert report.has_regression is True

    def test_binary_flip_up_is_not_a_regression(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S6": {"score": 0.0, "scoring": "binary"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S6", "score": 1.0, "scoring": "binary"}]),
            baseline_path=path,
        )
        assert report.has_drift is True
        assert report.has_regression is False

    def test_drop_within_threshold_is_not_a_regression(self, tmp_path: Path):
        """The threshold still governs: a sub-threshold dip is not drift, so
        it is not a regression either. Regression is a SUBSET of drift, never
        an independent trigger."""
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S1": {"score": 1.0, "scoring": "semantic"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S1", "score": 0.9, "scoring": "semantic"}]),
            baseline_path=path,
        )
        assert report.has_drift is False
        assert report.has_regression is False


# --- enforce_no_regression: the gate that actually fails --------------------


class TestEnforceNoRegression:
    """The half with teeth.

    ``compare_to_baseline`` classified drift for three separate rot events
    (a closed brief, a retired path, a renamed field) and returned zero every
    time, because nothing ever raised on what it found. These tests pin the
    raise.
    """

    def test_raises_on_regression(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S6": {"score": 1.0, "scoring": "binary"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S6", "score": 0.0, "scoring": "binary"}]),
            baseline_path=path,
        )
        with pytest.raises(BaselineRegression) as excinfo:
            enforce_no_regression(report)
        # The message must be actionable without opening the run JSON.
        assert "S6" in str(excinfo.value)
        assert "baseline=1.0" in str(excinfo.value)
        assert "new=0.0" in str(excinfo.value)

    def test_regression_is_an_assertion_error(self, tmp_path: Path):
        """pytest renders an AssertionError as a failure, not an internal
        error -- a regression is a failed expectation, not a crash."""
        assert issubclass(BaselineRegression, AssertionError)

    def test_silent_on_improvement(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload(
            {"S1": {"score": 0.0, "scoring": "semantic"}}
        ))
        report = compare_to_baseline(
            _run_payload([{"id": "S1", "score": 1.0, "scoring": "semantic"}]),
            baseline_path=path,
        )
        assert report.has_drift is True
        enforce_no_regression(report)  # must not raise

    def test_silent_on_new_case(self, tmp_path: Path):
        """A case with no baseline entry cannot regress -- there is nothing to
        regress from. ``test_evals.test_every_case_has_a_baseline_entry`` is
        what stops a case from living permanently in this blind spot."""
        path = _write_baseline(tmp_path, _baseline_payload({}))
        report = compare_to_baseline(
            _run_payload([{"id": "S99", "score": 0.0, "scoring": "binary"}]),
            baseline_path=path,
        )
        enforce_no_regression(report)  # must not raise

    def test_silent_on_missing_baseline(self, tmp_path: Path):
        report = compare_to_baseline(
            _run_payload([{"id": "S1", "score": 0.0, "scoring": "semantic"}]),
            baseline_path=tmp_path / "absent.json",
        )
        assert report.missing_baseline is True
        enforce_no_regression(report)  # must not raise

    def test_reports_every_offender_not_just_the_first(self, tmp_path: Path):
        path = _write_baseline(tmp_path, _baseline_payload({
            "S1": {"score": 1.0, "scoring": "semantic"},
            "S4": {"score": 1.0, "scoring": "binary"},
            "S6": {"score": 1.0, "scoring": "binary"},
        }))
        report = compare_to_baseline(
            _run_payload([
                {"id": "S1", "score": 0.0, "scoring": "semantic"},
                {"id": "S4", "score": 1.0, "scoring": "binary"},
                {"id": "S6", "score": 0.0, "scoring": "binary"},
            ]),
            baseline_path=path,
        )
        with pytest.raises(BaselineRegression) as excinfo:
            enforce_no_regression(report)
        message = str(excinfo.value)
        assert "S1" in message and "S6" in message
        assert "2 eval case(s)" in message


# --- write_baseline_candidate ----------------------------------------------


class TestWriteBaselineCandidate:
    """Candidate snapshots are written for manual promotion."""

    def test_writes_candidate_and_returns_path(self, tmp_path: Path):
        candidate = tmp_path / "baseline.candidate.json"
        new = _run_payload(
            [
                {"id": "S1", "score": 0.9, "scoring": "semantic"},
                {"id": "S5", "score": 1.0, "scoring": "binary"},
            ]
        )
        path = write_baseline_candidate(new, path=candidate)
        assert path == candidate
        assert path.is_file()

        payload = json.loads(path.read_text())
        assert payload["version"] == 1
        assert payload["cases"]["S1"]["score"] == 0.9
        assert payload["cases"]["S1"]["scoring"] == "semantic"
        assert payload["cases"]["S5"]["score"] == 1.0
        assert payload["cases"]["S5"]["scoring"] == "binary"

    def test_does_not_overwrite_live_baseline(self, tmp_path: Path):
        # Promotion must be manual: writing a candidate leaves baseline
        # untouched.
        baseline = _baseline_payload(
            {"S1": {"score": 0.9, "scoring": "semantic"}}
        )
        baseline_path = _write_baseline(tmp_path, baseline)
        pre = baseline_path.read_text()

        new = _run_payload(
            [{"id": "S1", "score": 0.2, "scoring": "semantic"}]
        )
        candidate = write_baseline_candidate(
            new, path=tmp_path / "baseline.candidate.json"
        )
        assert candidate.name == "baseline.candidate.json"
        assert baseline_path.read_text() == pre

    def test_creates_parent_dir_on_demand(self, tmp_path: Path):
        target = tmp_path / "fresh" / "baseline.candidate.json"
        assert not target.parent.exists()
        new = _run_payload(
            [{"id": "S1", "score": 0.75, "scoring": "semantic"}]
        )
        path = write_baseline_candidate(new, path=target)
        assert path.is_file()
        assert path.parent.is_dir()

    def test_candidate_roundtrips_via_load_baseline(self, tmp_path: Path):
        # A freshly written candidate is a valid baseline input.
        candidate = tmp_path / "baseline.candidate.json"
        new = _run_payload(
            [{"id": "S1", "score": 0.8, "scoring": "semantic"}]
        )
        write_baseline_candidate(new, path=candidate)
        loaded = load_baseline(candidate)
        assert loaded["cases"]["S1"]["score"] == 0.8
