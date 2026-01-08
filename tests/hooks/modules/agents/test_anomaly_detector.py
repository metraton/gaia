#!/usr/bin/env python3
"""
Tests for Anomaly Detection.

Validates:
1. Anomaly detection logic
2. Gaia signaling
3. Analysis signal management
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.anomaly_detector import (
    Anomaly,
    AnomalySeverity,
    detect_anomalies,
    signal_gaia_analysis,
    check_analysis_signal,
    clear_analysis_signal,
    ANOMALY_THRESHOLDS,
)
from modules.agents.subagent_metrics import SubagentMetrics


class TestAnomalySeverity:
    """Test AnomalySeverity enum."""

    def test_severity_levels(self):
        """Test severity levels exist."""
        assert AnomalySeverity.WARNING.value == "warning"
        assert AnomalySeverity.ERROR.value == "error"
        assert AnomalySeverity.CRITICAL.value == "critical"


class TestAnomaly:
    """Test Anomaly dataclass."""

    def test_creates_anomaly(self):
        """Test creating anomaly."""
        anomaly = Anomaly(
            anomaly_type="slow_execution",
            severity=AnomalySeverity.WARNING,
            message="Agent took 150s",
            agent="terraform-architect"
        )
        assert anomaly.anomaly_type == "slow_execution"
        assert anomaly.severity == AnomalySeverity.WARNING

    def test_to_dict(self):
        """Test to_dict conversion."""
        anomaly = Anomaly(
            anomaly_type="execution_failure",
            severity=AnomalySeverity.ERROR,
            message="Command failed",
            agent="gitops-operator",
            details={"exit_code": 1}
        )
        result = anomaly.to_dict()
        assert result["type"] == "execution_failure"
        assert result["severity"] == "error"
        assert result["details"]["exit_code"] == 1


class TestDetectAnomalies:
    """Test detect_anomalies function."""

    def test_detects_slow_execution(self):
        """Test detects slow execution (>120s)."""
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="test",
            task_id="T001",
            agent="terraform-architect",
            tier="T2",
            duration_ms=150000,  # 150 seconds
            exit_code=0,
            output_length=1000,
            tags=[]
        )

        anomalies = detect_anomalies(metrics)

        slow_anomaly = next((a for a in anomalies if a.anomaly_type == "slow_execution"), None)
        assert slow_anomaly is not None
        assert slow_anomaly.severity == AnomalySeverity.WARNING

    def test_detects_execution_failure(self):
        """Test detects execution failure."""
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="test",
            task_id="T001",
            agent="gitops-operator",
            tier="T1",
            duration_ms=5000,
            exit_code=1,  # Failed
            output_length=100,
            tags=[]
        )

        anomalies = detect_anomalies(metrics)

        failure_anomaly = next((a for a in anomalies if a.anomaly_type == "execution_failure"), None)
        assert failure_anomaly is not None
        assert failure_anomaly.severity == AnomalySeverity.ERROR

    def test_no_anomaly_for_normal_execution(self):
        """Test no anomaly for normal execution."""
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="test",
            task_id="T001",
            agent="devops-developer",
            tier="T0",
            duration_ms=30000,  # 30 seconds - normal
            exit_code=0,
            output_length=500,
            tags=[]
        )

        anomalies = detect_anomalies(metrics)

        # No slow execution anomaly (30s < 120s)
        slow_anomaly = next((a for a in anomalies if a.anomaly_type == "slow_execution"), None)
        assert slow_anomaly is None

        # No failure anomaly (exit_code == 0)
        failure_anomaly = next((a for a in anomalies if a.anomaly_type == "execution_failure"), None)
        assert failure_anomaly is None


class TestSignalGaiaAnalysis:
    """Test signal_gaia_analysis function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp memory directory."""
        memory_dir = tmp_path / "memory" / "workflow-episodic"
        memory_dir.mkdir(parents=True)
        with patch("modules.agents.anomaly_detector.get_memory_dir", return_value=memory_dir):
            yield memory_dir

    def test_creates_signal_file(self, setup):
        """Test creates signal file."""
        anomalies = [
            Anomaly(
                anomaly_type="slow_execution",
                severity=AnomalySeverity.WARNING,
                message="Test"
            )
        ]
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="test",
            task_id="T001",
            agent="test",
            tier="T0",
            duration_ms=150000,
            exit_code=0,
            output_length=100,
            tags=[]
        )

        result = signal_gaia_analysis(anomalies, metrics)

        assert result is True
        signal_file = setup / "signals" / "needs_analysis.flag"
        assert signal_file.exists()

    def test_signal_contains_anomaly_data(self, setup):
        """Test signal file contains anomaly data."""
        anomalies = [
            Anomaly(
                anomaly_type="execution_failure",
                severity=AnomalySeverity.ERROR,
                message="Failed",
                agent="terraform-architect"
            )
        ]
        metrics = SubagentMetrics(
            timestamp="2024-01-01T10:00:00",
            session_id="test",
            task_id="T002",
            agent="terraform-architect",
            tier="T3",
            duration_ms=5000,
            exit_code=1,
            output_length=200,
            tags=[]
        )

        signal_gaia_analysis(anomalies, metrics)

        signal_file = setup / "signals" / "needs_analysis.flag"
        with open(signal_file) as f:
            data = json.load(f)

        assert "anomalies" in data
        assert len(data["anomalies"]) == 1
        assert data["anomalies"][0]["type"] == "execution_failure"


class TestCheckAnalysisSignal:
    """Test check_analysis_signal function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp memory directory."""
        memory_dir = tmp_path / "memory" / "workflow-episodic"
        memory_dir.mkdir(parents=True)
        with patch("modules.agents.anomaly_detector.get_memory_dir", return_value=memory_dir):
            yield memory_dir

    def test_returns_none_when_no_signal(self, setup):
        """Test returns None when no signal exists."""
        result = check_analysis_signal()
        assert result is None

    def test_returns_signal_data(self, setup):
        """Test returns signal data when exists."""
        signals_dir = setup / "signals"
        signals_dir.mkdir()
        signal_file = signals_dir / "needs_analysis.flag"

        signal_data = {"anomalies": [], "timestamp": "2024-01-01T10:00:00"}
        with open(signal_file, "w") as f:
            json.dump(signal_data, f)

        result = check_analysis_signal()
        assert result is not None
        assert "anomalies" in result


class TestClearAnalysisSignal:
    """Test clear_analysis_signal function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp memory directory."""
        memory_dir = tmp_path / "memory" / "workflow-episodic"
        memory_dir.mkdir(parents=True)
        with patch("modules.agents.anomaly_detector.get_memory_dir", return_value=memory_dir):
            yield memory_dir

    def test_clears_signal_file(self, setup):
        """Test clears signal file."""
        signals_dir = setup / "signals"
        signals_dir.mkdir()
        signal_file = signals_dir / "needs_analysis.flag"
        signal_file.write_text("{}")

        assert signal_file.exists()

        result = clear_analysis_signal()

        assert result is True
        assert not signal_file.exists()

    def test_succeeds_when_no_signal(self, setup):
        """Test succeeds when no signal exists."""
        result = clear_analysis_signal()
        assert result is True


class TestAnomalyThresholds:
    """Test anomaly thresholds configuration."""

    def test_slow_execution_threshold(self):
        """Test slow execution threshold is defined."""
        assert "slow_execution_ms" in ANOMALY_THRESHOLDS
        assert ANOMALY_THRESHOLDS["slow_execution_ms"] == 120000

    def test_consecutive_failures_threshold(self):
        """Test consecutive failures threshold is defined."""
        assert "consecutive_failures" in ANOMALY_THRESHOLDS
        assert ANOMALY_THRESHOLDS["consecutive_failures"] == 3
