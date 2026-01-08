#!/usr/bin/env python3
"""
Tests for Metrics Collection.

Validates:
1. MetricsCollector class
2. Metric recording
3. Summary generation
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.metrics import (
    MetricsCollector,
    get_metrics_collector,
    record_metric,
    generate_summary,
)


class TestMetricsCollector:
    """Test MetricsCollector class."""

    @pytest.fixture
    def metrics_dir(self, tmp_path):
        """Create temp metrics directory."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        return metrics_dir

    @pytest.fixture
    def collector(self, metrics_dir):
        """Create collector with temp directory."""
        return MetricsCollector(metrics_dir=metrics_dir)

    def test_records_execution(self, collector, metrics_dir):
        """Test recording execution creates file."""
        collector.record_execution(
            tool_name="bash",
            command="kubectl get pods",
            duration=1.5,
            success=True,
            tier="T0"
        )

        # Check metrics file exists
        metrics_files = list(metrics_dir.glob("metrics-*.jsonl"))
        assert len(metrics_files) > 0

    def test_record_contains_expected_fields(self, collector, metrics_dir):
        """Test record contains expected fields."""
        collector.record_execution(
            tool_name="bash",
            command="terraform plan",
            duration=5.0,
            success=True,
            tier="T1"
        )

        metrics_file = next(metrics_dir.glob("metrics-*.jsonl"))
        with open(metrics_file) as f:
            record = json.loads(f.readline())

        assert record["tool_name"] == "bash"
        assert record["success"] is True
        assert record["tier"] == "T1"
        assert "duration_ms" in record
        assert "timestamp" in record
        assert "command_type" in record


class TestCommandClassification:
    """Test command classification for metrics."""

    @pytest.fixture
    def collector(self, tmp_path):
        """Create collector."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        return MetricsCollector(metrics_dir=metrics_dir)

    @pytest.mark.parametrize("command,expected_type", [
        ("terraform plan", "terraform"),
        ("kubectl get pods", "kubernetes"),
        ("helm list", "helm"),
        ("flux get all", "flux"),
        ("gcloud compute instances list", "gcp"),
        ("aws ec2 describe-instances", "aws"),
        ("docker ps", "docker"),
        ("git status", "git"),
        ("ls -la", "general"),
    ])
    def test_classifies_commands(self, collector, command, expected_type):
        """Test command type classification."""
        result = collector._classify_command(command)
        assert result == expected_type


class TestGenerateSummary:
    """Test summary generation."""

    @pytest.fixture
    def metrics_dir(self, tmp_path):
        """Create temp metrics directory."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        return metrics_dir

    @pytest.fixture
    def collector(self, metrics_dir):
        """Create collector with temp directory."""
        return MetricsCollector(metrics_dir=metrics_dir)

    def test_empty_summary(self, collector):
        """Test summary with no data."""
        summary = collector.generate_summary(days=7)
        assert summary["total_executions"] == 0
        assert summary["success_rate"] == 0.0

    def test_summary_with_data(self, collector, metrics_dir):
        """Test summary with recorded data."""
        # Record some executions
        collector.record_execution("bash", "ls", 0.1, True, "T0")
        collector.record_execution("bash", "pwd", 0.05, True, "T0")
        collector.record_execution("bash", "failing", 1.0, False, "T3")

        summary = collector.generate_summary(days=7)
        assert summary["total_executions"] == 3
        assert summary["success_rate"] > 0
        assert "top_commands" in summary
        assert "tier_distribution" in summary

    def test_summary_calculates_success_rate(self, collector, metrics_dir):
        """Test success rate calculation."""
        # 2 successes, 1 failure = 66.67%
        collector.record_execution("bash", "cmd1", 0.1, True, "T0")
        collector.record_execution("bash", "cmd2", 0.1, True, "T0")
        collector.record_execution("bash", "cmd3", 0.1, False, "T0")

        summary = collector.generate_summary(days=7)
        # Allow for floating point comparison
        assert 0.66 <= summary["success_rate"] <= 0.67

    def test_summary_calculates_average_duration(self, collector, metrics_dir):
        """Test average duration calculation."""
        collector.record_execution("bash", "cmd1", 1.0, True, "T0")  # 1000ms
        collector.record_execution("bash", "cmd2", 2.0, True, "T0")  # 2000ms
        collector.record_execution("bash", "cmd3", 3.0, True, "T0")  # 3000ms

        summary = collector.generate_summary(days=7)
        # Average should be 2000ms
        assert 1900 <= summary["avg_duration_ms"] <= 2100

    def test_summary_includes_tier_distribution(self, collector, metrics_dir):
        """Test tier distribution in summary."""
        collector.record_execution("bash", "cmd1", 0.1, True, "T0")
        collector.record_execution("bash", "cmd2", 0.1, True, "T0")
        collector.record_execution("bash", "cmd3", 0.1, True, "T1")

        summary = collector.generate_summary(days=7)
        assert "T0" in summary["tier_distribution"]
        assert summary["tier_distribution"]["T0"] == 2

    def test_summary_respects_time_window(self, collector, metrics_dir):
        """Test summary respects time window."""
        # Record current metric
        collector.record_execution("bash", "recent", 0.1, True, "T0")

        # Manually create old metric
        metrics_file = metrics_dir / f"metrics-{datetime.now().strftime('%Y-%m')}.jsonl"
        old_timestamp = (datetime.now() - timedelta(days=30)).isoformat()
        with open(metrics_file, "a") as f:
            f.write(json.dumps({
                "timestamp": old_timestamp,
                "tool_name": "bash",
                "command_type": "general",
                "duration_ms": 100,
                "success": True,
                "tier": "T0"
            }) + "\n")

        # Summary for last 7 days should only include recent
        summary = collector.generate_summary(days=7)
        assert summary["total_executions"] == 1


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        with patch("modules.audit.metrics.get_metrics_dir", return_value=metrics_dir):
            # Reset global collector
            import modules.audit.metrics as metrics_module
            metrics_module._metrics_collector = None
            yield metrics_dir

    def test_get_metrics_collector(self):
        """Test get_metrics_collector returns instance."""
        collector = get_metrics_collector()
        assert isinstance(collector, MetricsCollector)

    def test_get_metrics_collector_is_singleton(self):
        """Test get_metrics_collector returns same instance."""
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        assert collector1 is collector2

    def test_record_metric_function(self, setup):
        """Test record_metric convenience function."""
        record_metric(
            tool_name="bash",
            command="test",
            duration=0.5,
            success=True,
            tier="T0"
        )

        # Check metric was recorded
        metrics_files = list(setup.glob("metrics-*.jsonl"))
        assert len(metrics_files) > 0

    def test_generate_summary_function(self, setup):
        """Test generate_summary convenience function."""
        summary = generate_summary(days=7)
        assert isinstance(summary, dict)
        assert "total_executions" in summary
