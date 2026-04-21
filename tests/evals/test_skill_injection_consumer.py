"""Tests for :func:`tests.evals.graders.skill_injection_consumer` (T4).

Closes gap G5 from the plan: S7 ("skill_adherence") does not re-detect
pipe violations -- it consumes anomalies already emitted by
``hooks/modules/agents/skill_injection_verifier`` into the audit stream.
This test suite exercises the consumer against the three fixture classes
called out in the plan:

(a) audit JSONL with the expected anomaly => pass (``present=True``)
(b) audit JSONL without the anomaly when one was expected => fail
(c) audit JSONL with the anomaly when none was expected => fail

Fixtures live under ``tests/evals/fixtures/audit/`` alongside the T3c
tool-trace fixtures, since both graders read the same audit stream.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.graders import GradeResult, skill_injection_consumer


FIXTURES = Path(__file__).parent / "fixtures" / "audit"

PIPE_DETECTED = FIXTURES / "skill_injection_pipe_detected.jsonl"
CLEAN = FIXTURES / "skill_injection_clean.jsonl"


# ---------------------------------------------------------------------------
# Fixture sanity -- catch drift early
# ---------------------------------------------------------------------------


def test_fixtures_exist():
    assert PIPE_DETECTED.exists(), f"missing fixture: {PIPE_DETECTED}"
    assert CLEAN.exists(), f"missing fixture: {CLEAN}"


# ---------------------------------------------------------------------------
# (a) anomaly present + expected present => pass
# ---------------------------------------------------------------------------


def test_anomaly_present_when_expected_passes():
    """S7 happy path -- the verifier flagged command-execution, the catalog
    asked for that anomaly, consumer reports pass.
    """
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert isinstance(result, GradeResult)
    assert result.passed is True
    assert result.score == 1.0
    assert any("matching anomaly" in r for r in result.reasons)


def test_anomaly_present_alias_accepted():
    """The verifier stamps ``skill_injection_gap``; the catalog uses
    ``skill_injection_anomaly``. Either spelling resolves to the same
    anomaly.
    """
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_gap",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is True
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# (b) anomaly absent when expected present => fail
# ---------------------------------------------------------------------------


def test_anomaly_absent_when_expected_fails():
    result = skill_injection_consumer(
        audit_paths=[CLEAN],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is False
    assert result.score == 0.0
    assert any("none found" in r or "expected anomaly" in r for r in result.reasons)


def test_empty_audit_paths_and_expected_present_fails():
    """No audit input at all still fails a ``present=True`` expectation --
    absence of evidence is evidence of absence for this contract.
    """
    result = skill_injection_consumer(
        audit_paths=[],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is False
    assert result.score == 0.0


def test_wrong_skill_does_not_match():
    """Anomaly exists but mentions a different skill -- not a match."""
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "agent-protocol",  # fixture lists command-execution
            "present": True,
        },
    )
    assert result.passed is False
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# (c) anomaly present when NOT expected => fail
# ---------------------------------------------------------------------------


def test_anomaly_present_when_not_expected_fails():
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": False,
        },
    )
    assert result.passed is False
    assert result.score == 0.0
    assert any("unexpected anomaly" in r for r in result.reasons)


def test_anomaly_absent_when_not_expected_passes():
    """Symmetric happy path -- clean audit + present=False."""
    result = skill_injection_consumer(
        audit_paths=[CLEAN],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": False,
        },
    )
    assert result.passed is True
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# Flat per-line shape (non-wrapper) support
# ---------------------------------------------------------------------------


def test_flat_per_line_anomaly_record(tmp_path: Path):
    """Fixtures may also ship a flat anomaly record per line, without the
    ``workflow_auditor`` wrapper. The consumer accepts both shapes.
    """
    flat = tmp_path / "skill_injection_flat.jsonl"
    flat.write_text(
        json.dumps({
            "type": "skill_injection_gap",
            "severity": "advisory",
            "agent_type": "developer",
            "missing_skills": ["command-execution"],
            "message": "flat record",
        }) + "\n",
        encoding="utf-8",
    )
    result = skill_injection_consumer(
        audit_paths=[flat],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is True
    assert result.score == 1.0


def test_flat_record_with_bare_skill_field(tmp_path: Path):
    """A flat anomaly may pin a single skill via a ``skill`` field rather
    than a ``missing_skills`` list -- this shape is reserved for future
    anomaly types and must match by equality.
    """
    flat = tmp_path / "skill_injection_skill_field.jsonl"
    flat.write_text(
        json.dumps({
            "type": "skill_injection_gap",
            "skill": "command-execution",
            "severity": "advisory",
        }) + "\n",
        encoding="utf-8",
    )
    result = skill_injection_consumer(
        audit_paths=[flat],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# Mixed / multiple sources
# ---------------------------------------------------------------------------


def test_multiple_audit_paths_merged():
    """When multiple audit files are passed, anomalies from any file
    satisfy the ``present=True`` expectation.
    """
    result = skill_injection_consumer(
        audit_paths=[CLEAN, PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is True


def test_missing_audit_file_is_skipped(tmp_path: Path):
    """A non-existent path is silently skipped so partial inputs from a
    slow dispatch don't spuriously fail.
    """
    missing = tmp_path / "does-not-exist.jsonl"
    result = skill_injection_consumer(
        audit_paths=[missing, PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is True


def test_malformed_lines_ignored(tmp_path: Path):
    """Partial JSON (e.g. write interrupted by kill -9) must not crash
    the consumer."""
    noisy = tmp_path / "skill_injection_noisy.jsonl"
    noisy.write_text(
        "this is not json\n"
        + json.dumps({"tool_name": "Bash", "command": "ls"}) + "\n"  # no type
        + "{broken: true,\n"  # broken JSON
        + json.dumps({
            "type": "skill_injection_gap",
            "missing_skills": ["command-execution"],
        }) + "\n",
        encoding="utf-8",
    )
    result = skill_injection_consumer(
        audit_paths=[noisy],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
        },
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# DSL validation -- catalog typos must fail loudly
# ---------------------------------------------------------------------------


def test_unknown_key_fails_loud():
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect={
            "anomaly_type": "skill_injection_anomaly",
            "skill": "command-execution",
            "present": True,
            "typo_field": "oops",
        },
    )
    assert result.passed is False
    assert any("unknown keys" in r for r in result.reasons)


@pytest.mark.parametrize(
    "missing",
    [
        {"skill": "command-execution", "present": True},
        {"anomaly_type": "skill_injection_anomaly", "present": True},
        {"anomaly_type": "skill_injection_anomaly", "skill": "command-execution"},
    ],
)
def test_missing_required_key_fails(missing):
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect=missing,
    )
    assert result.passed is False
    assert any("missing required keys" in r for r in result.reasons)


@pytest.mark.parametrize(
    "expect_bad",
    [
        {"anomaly_type": "", "skill": "command-execution", "present": True},
        {"anomaly_type": 123, "skill": "command-execution", "present": True},
        {"anomaly_type": "skill_injection_anomaly", "skill": "", "present": True},
        {"anomaly_type": "skill_injection_anomaly", "skill": None, "present": True},
        {"anomaly_type": "skill_injection_anomaly", "skill": "x", "present": "yes"},
        {"anomaly_type": "skill_injection_anomaly", "skill": "x", "present": 1},
    ],
)
def test_bad_value_types_fail(expect_bad):
    result = skill_injection_consumer(
        audit_paths=[PIPE_DETECTED],
        anomaly_expect=expect_bad,
    )
    assert result.passed is False
    assert result.score == 0.0
