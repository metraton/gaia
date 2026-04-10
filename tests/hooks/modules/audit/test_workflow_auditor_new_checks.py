#!/usr/bin/env python3
"""
Tests for transcript-analysis anomaly checks in workflow_auditor.

Covers all 9 new checks added in T009, plus backward compatibility
when transcript_analysis is None.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.transcript_analyzer import (
    DuplicateCall,
    ToolCall,
    TranscriptAnalysis,
)
from modules.audit.workflow_auditor import audit
from modules.core.paths import clear_path_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_workflow_env(tmp_path, monkeypatch):
    """Isolate workflow writes so consecutive_failures check doesn't read stale data."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("GAIA_WRITE_WORKFLOW_METRICS", "1")
    yield tmp_path
    clear_path_cache()


def _base_metrics(**overrides):
    """Minimal metrics dict accepted by audit()."""
    m = {"agent": "developer", "task_id": "t-001", "exit_code": 0}
    m.update(overrides)
    return m


def _base_analysis(**overrides) -> TranscriptAnalysis:
    """Build a TranscriptAnalysis with sensible defaults and overrides."""
    ta = TranscriptAnalysis()
    for k, v in overrides.items():
        setattr(ta, k, v)
    return ta


# ===========================================================================
# 1. investigation_skip
# ===========================================================================


class TestInvestigationSkip:
    def test_triggers_when_first_tool_is_bash(self):
        ta = _base_analysis(first_tool_name="Bash")
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "investigation_skip" in types
        match = next(a for a in anomalies if a["type"] == "investigation_skip")
        assert match["severity"] == "warning"

    def test_no_anomaly_when_first_tool_is_read(self):
        ta = _base_analysis(first_tool_name="Read")
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "investigation_skip" not in types

    def test_no_anomaly_when_first_tool_is_none(self):
        ta = _base_analysis(first_tool_name=None)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "investigation_skip" not in types


# ===========================================================================
# 2. context_ignored
# ===========================================================================


class TestContextIgnored:
    def test_triggers_when_no_context_paths_in_first_call(self):
        tc = ToolCall(index=1, tool_name="Read", arguments={"file_path": "/tmp/foo"})
        ta = _base_analysis(tool_sequence=[tc])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "context_ignored" in types
        match = next(a for a in anomalies if a["type"] == "context_ignored")
        assert match["severity"] == "warning"

    def test_no_anomaly_when_first_call_references_context(self):
        tc = ToolCall(
            index=1,
            tool_name="Read",
            arguments={"file_path": "/project/.claude/config.json"},
        )
        ta = _base_analysis(tool_sequence=[tc])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "context_ignored" not in types

    def test_no_anomaly_when_no_tool_sequence(self):
        ta = _base_analysis(tool_sequence=[])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "context_ignored" not in types


# ===========================================================================
# 3. context_update_missing
# ===========================================================================


class TestContextUpdateMissing:
    def test_triggers_when_skill_injected_but_no_update_block(self):
        ta = _base_analysis(skills_injected=["context-updater", "investigation"])
        anomalies = audit(
            _base_metrics(),
            agent_output="some output without context update",
            transcript_analysis=ta,
        )
        types = [a["type"] for a in anomalies]
        assert "context_update_missing" in types
        match = next(a for a in anomalies if a["type"] == "context_update_missing")
        assert match["severity"] == "info"

    def test_no_anomaly_when_context_update_present(self):
        ta = _base_analysis(skills_injected=["context-updater"])
        anomalies = audit(
            _base_metrics(),
            agent_output="blah CONTEXT_UPDATE: {} blah",
            transcript_analysis=ta,
        )
        types = [a["type"] for a in anomalies]
        assert "context_update_missing" not in types

    def test_no_anomaly_when_skill_not_injected(self):
        ta = _base_analysis(skills_injected=["investigation"])
        anomalies = audit(
            _base_metrics(),
            agent_output="no update here",
            transcript_analysis=ta,
        )
        types = [a["type"] for a in anomalies]
        assert "context_update_missing" not in types


# ===========================================================================
# 4. excessive_tool_calls
# ===========================================================================


class TestExcessiveToolCalls:
    def test_triggers_when_over_75(self):
        ta = _base_analysis(tool_call_count=76)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "excessive_tool_calls" in types
        match = next(a for a in anomalies if a["type"] == "excessive_tool_calls")
        assert match["severity"] == "warning"
        assert "76" in match["message"]

    def test_no_anomaly_at_boundary(self):
        ta = _base_analysis(tool_call_count=75)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "excessive_tool_calls" not in types

    def test_no_anomaly_when_zero(self):
        ta = _base_analysis(tool_call_count=0)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "excessive_tool_calls" not in types


# ===========================================================================
# 5. token_budget
# ===========================================================================


class TestTokenBudget:
    def test_triggers_when_over_200k(self):
        ta = _base_analysis(cache_creation_tokens=200001)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "token_budget" in types
        match = next(a for a in anomalies if a["type"] == "token_budget")
        assert match["severity"] == "info"

    def test_no_anomaly_at_boundary(self):
        ta = _base_analysis(cache_creation_tokens=200000)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "token_budget" not in types

    def test_no_anomaly_when_zero(self):
        ta = _base_analysis(cache_creation_tokens=0)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "token_budget" not in types


# ===========================================================================
# 6. pipe_retroactive
# ===========================================================================


class TestPipeRetroactive:
    def test_triggers_per_pipe_command(self):
        ta = _base_analysis(pipe_commands=["ls | grep foo", "cat x | wc -l"])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        pipe_anomalies = [a for a in anomalies if a["type"] == "pipe_retroactive"]
        assert len(pipe_anomalies) == 2
        assert all(a["severity"] == "warning" for a in pipe_anomalies)

    def test_no_anomaly_when_no_pipes(self):
        ta = _base_analysis(pipe_commands=[])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "pipe_retroactive" not in types

    def test_long_command_truncated_in_message(self):
        long_cmd = "x" * 200
        ta = _base_analysis(pipe_commands=[long_cmd])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        match = next(a for a in anomalies if a["type"] == "pipe_retroactive")
        # Truncation at 120 chars + "..."
        assert "..." in match["message"]
        assert len(match["message"]) < len(long_cmd) + 100


# ===========================================================================
# 7. model_mismatch
# ===========================================================================


class TestModelMismatch:
    def test_triggers_when_models_differ(self):
        ta = _base_analysis(model="claude-sonnet-4-20250514")
        metrics = _base_metrics(
            default_skills_snapshot={"model": "claude-opus-4-20250514", "skills": []}
        )
        anomalies = audit(metrics, transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "model_mismatch" in types
        match = next(a for a in anomalies if a["type"] == "model_mismatch")
        assert match["severity"] == "info"

    def test_no_anomaly_when_models_match(self):
        ta = _base_analysis(model="claude-opus-4-20250514")
        metrics = _base_metrics(
            default_skills_snapshot={"model": "claude-opus-4-20250514", "skills": []}
        )
        anomalies = audit(metrics, transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "model_mismatch" not in types

    def test_no_anomaly_when_no_snapshot(self):
        ta = _base_analysis(model="claude-opus-4-20250514")
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "model_mismatch" not in types

    def test_no_anomaly_when_transcript_model_empty(self):
        ta = _base_analysis(model="")
        metrics = _base_metrics(
            default_skills_snapshot={"model": "claude-opus-4-20250514", "skills": []}
        )
        anomalies = audit(metrics, transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "model_mismatch" not in types


# ===========================================================================
# 8. skill_order
# ===========================================================================


class TestSkillOrder:
    def test_triggers_when_order_differs(self):
        ta = _base_analysis(skills_injected=["investigation", "agent-protocol"])
        metrics = _base_metrics(
            default_skills_snapshot={
                "model": "",
                "skills": ["agent-protocol", "investigation"],
            }
        )
        anomalies = audit(metrics, transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "skill_order" in types
        match = next(a for a in anomalies if a["type"] == "skill_order")
        assert match["severity"] == "info"

    def test_no_anomaly_when_order_matches(self):
        ta = _base_analysis(skills_injected=["agent-protocol", "investigation"])
        metrics = _base_metrics(
            default_skills_snapshot={
                "model": "",
                "skills": ["agent-protocol", "investigation"],
            }
        )
        anomalies = audit(metrics, transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "skill_order" not in types

    def test_no_anomaly_when_no_expected_skills(self):
        ta = _base_analysis(skills_injected=["investigation"])
        metrics = _base_metrics(default_skills_snapshot={"model": "", "skills": []})
        anomalies = audit(metrics, transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "skill_order" not in types

    def test_no_anomaly_when_no_snapshot(self):
        ta = _base_analysis(skills_injected=["investigation"])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "skill_order" not in types


# ===========================================================================
# 9. duplicate_tools
# ===========================================================================


class TestDuplicateTools:
    def test_triggers_when_duplicates_present(self):
        dups = [
            DuplicateCall(tool_name="Read", arguments_hash="abc123", indices=[1, 3]),
            DuplicateCall(tool_name="Bash", arguments_hash="def456", indices=[2, 4, 6]),
        ]
        ta = _base_analysis(duplicate_tool_calls=dups)
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "duplicate_tools" in types
        match = next(a for a in anomalies if a["type"] == "duplicate_tools")
        assert match["severity"] == "info"
        assert "Read(x2)" in match["message"]
        assert "Bash(x3)" in match["message"]

    def test_no_anomaly_when_no_duplicates(self):
        ta = _base_analysis(duplicate_tool_calls=[])
        anomalies = audit(_base_metrics(), transcript_analysis=ta)
        types = [a["type"] for a in anomalies]
        assert "duplicate_tools" not in types


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestBackwardCompatibility:
    """audit() without transcript_analysis behaves like the original detector."""

    def test_no_transcript_checks_without_analysis(self):
        """When transcript_analysis is None, no transcript-based anomalies appear."""
        metrics = _base_metrics(exit_code=0)
        anomalies = audit(metrics, agent_output="", transcript_analysis=None)
        transcript_types = {
            "investigation_skip",
            "context_ignored",
            "context_update_missing",
            "excessive_tool_calls",
            "token_budget",
            "pipe_retroactive",
            "model_mismatch",
            "skill_order",
            "duplicate_tools",
        }
        found_types = {a["type"] for a in anomalies}
        assert found_types.isdisjoint(transcript_types), (
            f"Transcript anomalies leaked without transcript_analysis: "
            f"{found_types & transcript_types}"
        )

    def test_default_param_is_none(self):
        """Calling audit() without the kwarg should work."""
        anomalies = audit(_base_metrics())
        assert isinstance(anomalies, list)

    def test_existing_checks_still_fire(self):
        """execution_failure should still trigger without transcript_analysis."""
        metrics = _base_metrics(exit_code=1)
        anomalies = audit(metrics)
        types = [a["type"] for a in anomalies]
        assert "execution_failure" in types

    def test_scope_escalation_still_fires(self):
        """scope_escalation check is independent of transcript_analysis."""
        anomalies = audit(
            _base_metrics(),
            rejected_sections=["infrastructure"],
        )
        types = [a["type"] for a in anomalies]
        assert "scope_escalation" in types
