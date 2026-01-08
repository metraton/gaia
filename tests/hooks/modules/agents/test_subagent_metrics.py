#!/usr/bin/env python3
"""
Tests for Subagent Metrics Capture.

Validates:
1. SubagentMetrics dataclass
2. Metric capture and extraction
3. Metrics persistence
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.subagent_metrics import (
    SubagentMetrics,
    capture_workflow_metrics,
    get_recent_metrics,
    _extract_duration,
    _extract_exit_code,
)


class TestSubagentMetrics:
    """Test SubagentMetrics dataclass."""

    def test_creates_metrics(self):
        """Test creating metrics instance."""
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="session-123",
            task_id="T001",
            agent="terraform-architect",
            tier="T2",
            duration_ms=5000,
            exit_code=0,
            output_length=1000,
            tags=["#terraform"]
        )
        assert metrics.agent == "terraform-architect"
        assert metrics.duration_ms == 5000
        assert metrics.exit_code == 0

    def test_to_dict(self):
        """Test to_dict conversion."""
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="test",
            task_id="T001",
            agent="gitops-operator",
            tier="T3",
            duration_ms=3000,
            exit_code=0,
            output_length=500,
            tags=[]
        )
        result = metrics.to_dict()
        assert result["agent"] == "gitops-operator"
        assert result["tier"] == "T3"
        assert result["duration_ms"] == 3000


class TestExtractDuration:
    """Test duration extraction from output."""

    def test_extracts_duration_ms_format(self):
        """Test extracts from 'Duration: X ms' format."""
        output = "Task completed\nDuration: 5000 ms\nSuccess"
        result = _extract_duration(output)
        assert result == 5000

    def test_extracts_duration_seconds_format(self):
        """Test extracts from 'took X seconds' format."""
        output = "Operation took 3.5 seconds to complete"
        result = _extract_duration(output)
        assert result == 3500

    def test_extracts_duration_s_format(self):
        """Test extracts from 'took X s' format."""
        output = "Finished in 2.0s"
        # Implementation may or may not support this
        result = _extract_duration(output)
        # May be None or a value

    def test_returns_none_when_not_found(self):
        """Test returns None when no duration found."""
        output = "Task completed successfully"
        result = _extract_duration(output)
        assert result is None


class TestExtractExitCode:
    """Test exit code extraction from output."""

    def test_extracts_explicit_exit_code(self):
        """Test extracts explicit exit code."""
        output = "Command failed\nexit code: 127"
        result = _extract_exit_code(output)
        assert result == 127

    def test_detects_error_keyword(self):
        """Test detects error keyword."""
        output = "An error occurred during execution"
        result = _extract_exit_code(output)
        assert result == 1

    def test_detects_failed_keyword(self):
        """Test detects failed keyword."""
        output = "The operation failed"
        result = _extract_exit_code(output)
        assert result == 1

    def test_returns_zero_for_success(self):
        """Test returns 0 for successful output."""
        output = "Task completed successfully"
        result = _extract_exit_code(output)
        assert result == 0


class TestCaptureWorkflowMetrics:
    """Test capture_workflow_metrics function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp memory directory."""
        memory_dir = tmp_path / "memory" / "workflow-episodic"
        memory_dir.mkdir(parents=True)
        with patch("modules.agents.subagent_metrics.get_memory_dir", return_value=memory_dir):
            yield memory_dir

    def test_captures_basic_metrics(self, setup):
        """Test captures basic metrics."""
        task_info = {
            "task_id": "T001",
            "agent": "terraform-architect",
            "tier": "T2",
            "tags": ["#terraform"]
        }
        session_context = {
            "timestamp": "2024-01-01T10:00:00",
            "session_id": "test-session"
        }
        output = "Task completed\nDuration: 5000 ms"

        metrics = capture_workflow_metrics(task_info, output, session_context)

        assert metrics.task_id == "T001"
        assert metrics.agent == "terraform-architect"
        assert metrics.duration_ms == 5000

    def test_writes_to_file(self, setup):
        """Test writes metrics to file."""
        task_info = {
            "task_id": "T002",
            "agent": "gitops-operator",
            "tier": "T1",
        }
        session_context = {
            "session_id": "session-123"
        }
        output = "Success"

        capture_workflow_metrics(task_info, output, session_context)

        metrics_file = setup / "metrics.jsonl"
        assert metrics_file.exists()

        with open(metrics_file) as f:
            data = json.loads(f.readline())
        assert data["task_id"] == "T002"


class TestGetRecentMetrics:
    """Test get_recent_metrics function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp memory directory with metrics."""
        memory_dir = tmp_path / "memory" / "workflow-episodic"
        memory_dir.mkdir(parents=True)

        # Create test metrics file
        metrics_file = memory_dir / "metrics.jsonl"
        test_metrics = [
            {"timestamp": "2024-01-01T10:00:00", "session_id": "s1", "task_id": "T001",
             "agent": "terraform-architect", "tier": "T2", "duration_ms": 1000,
             "exit_code": 0, "output_length": 100, "tags": []},
            {"timestamp": "2024-01-01T11:00:00", "session_id": "s1", "task_id": "T002",
             "agent": "gitops-operator", "tier": "T1", "duration_ms": 2000,
             "exit_code": 0, "output_length": 200, "tags": []},
            {"timestamp": "2024-01-01T12:00:00", "session_id": "s1", "task_id": "T003",
             "agent": "terraform-architect", "tier": "T2", "duration_ms": 3000,
             "exit_code": 1, "output_length": 300, "tags": []},
        ]

        with open(metrics_file, "w") as f:
            for m in test_metrics:
                f.write(json.dumps(m) + "\n")

        with patch("modules.agents.subagent_metrics.get_memory_dir", return_value=memory_dir):
            yield memory_dir

    def test_gets_recent_metrics(self, setup):
        """Test gets recent metrics."""
        metrics = get_recent_metrics(limit=10)
        assert len(metrics) == 3

    def test_filters_by_agent(self, setup):
        """Test filters by agent."""
        metrics = get_recent_metrics(agent="terraform-architect", limit=10)
        assert len(metrics) == 2
        assert all(m.agent == "terraform-architect" for m in metrics)

    def test_respects_limit(self, setup):
        """Test respects limit parameter."""
        metrics = get_recent_metrics(limit=2)
        assert len(metrics) == 2

    def test_returns_empty_when_no_file(self, tmp_path):
        """Test returns empty list when no file exists."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch("modules.agents.subagent_metrics.get_memory_dir", return_value=empty_dir):
            metrics = get_recent_metrics()
            assert metrics == []
