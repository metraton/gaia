#!/usr/bin/env python3
"""Calibration tests for the excessive_tool_calls threshold.

The threshold is asserted against the measurement that justifies it -- the
2026-07-26 harness cuts and the low-risk band below the measured step -- rather
than against a literal, so a future edit to the number fails here unless the
recorded measurement moves with it.

Every case drives the real production entry point (``audit()`` fed a
``TranscriptAnalysis``), the same shape ``adapt_subagent_stop`` builds, so a
green run means the check fires on the payload the runtime actually produces.

Test names deliberately contain ``tool_calls``: the acceptance gate selects
with ``pytest -k tool_calls``, which is case-sensitive and would otherwise
deselect this file entirely and pass on unrelated tests.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.transcript_analyzer import TranscriptAnalysis
from modules.audit.workflow_auditor import (
    EXCESSIVE_TOOL_CALL_THRESHOLD,
    OBSERVED_HARNESS_CUT_TOOL_CALLS,
    audit,
)
from modules.core.paths import clear_path_cache

LOW_RISK_BAND = range(26, EXCESSIVE_TOOL_CALL_THRESHOLD + 1)


@pytest.fixture(autouse=True)
def isolated_workflow_env(tmp_path, monkeypatch):
    """Isolate workflow writes so consecutive_failures does not read stale data."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("GAIA_WRITE_WORKFLOW_METRICS", "1")
    yield tmp_path
    clear_path_cache()


def _anomaly_types(tool_call_count: int) -> list:
    analysis = TranscriptAnalysis()
    analysis.tool_call_count = tool_call_count
    metrics = {"agent": "developer", "task_id": "t-001", "exit_code": 0}
    return [a["type"] for a in audit(metrics, transcript_analysis=analysis)]


def test_threshold_sits_below_every_observed_cut_in_tool_calls():
    """The calibration itself: no measured cut may land under the threshold."""
    assert EXCESSIVE_TOOL_CALL_THRESHOLD < min(OBSERVED_HARNESS_CUT_TOOL_CALLS)


def test_threshold_is_the_top_of_the_low_risk_band_of_tool_calls():
    assert EXCESSIVE_TOOL_CALL_THRESHOLD == max(LOW_RISK_BAND)


@pytest.mark.parametrize("count", OBSERVED_HARNESS_CUT_TOOL_CALLS)
def test_every_observed_2026_07_26_cut_raises_excessive_tool_calls(count):
    assert "excessive_tool_calls" in _anomaly_types(count)


@pytest.mark.parametrize("count", LOW_RISK_BAND)
def test_low_risk_band_raises_no_excessive_tool_calls(count):
    assert "excessive_tool_calls" not in _anomaly_types(count)


def test_first_count_above_threshold_raises_excessive_tool_calls():
    assert "excessive_tool_calls" in _anomaly_types(EXCESSIVE_TOOL_CALL_THRESHOLD + 1)


def test_message_reports_both_the_count_and_the_threshold_of_tool_calls():
    analysis = TranscriptAnalysis()
    analysis.tool_call_count = OBSERVED_HARNESS_CUT_TOOL_CALLS[0]
    metrics = {"agent": "developer", "task_id": "t-001", "exit_code": 0}
    anomalies = audit(metrics, transcript_analysis=analysis)
    match = next(a for a in anomalies if a["type"] == "excessive_tool_calls")
    assert match["severity"] == "warning"
    assert str(OBSERVED_HARNESS_CUT_TOOL_CALLS[0]) in match["message"]
    assert str(EXCESSIVE_TOOL_CALL_THRESHOLD) in match["message"]
