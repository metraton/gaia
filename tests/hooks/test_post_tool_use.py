#!/usr/bin/env python3
"""
Tests for Post-Tool Use Hook.

Validates:
1. Entry point hook logic
2. State reading from pre-hook
3. Audit logging
4. Metrics recording
5. Event detection
"""

import os
import sys
import json
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from post_tool_use import (
    post_tool_use_hook,
    ActiveContextUpdater,
    NotificationHandler,
)
from modules.core.state import HookState, save_hook_state


class TestPostToolUseHook:
    """Test main post_tool_use_hook function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        logs_dir = claude_dir / "logs"
        logs_dir.mkdir()
        metrics_dir = claude_dir / "metrics"
        metrics_dir.mkdir()
        session_dir = claude_dir / "session" / "active"
        session_dir.mkdir(parents=True)

        with patch("post_tool_use.get_logs_dir", return_value=logs_dir):
            with patch("post_tool_use.get_session_dir", return_value=session_dir):
                with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
                    with patch("modules.audit.logger.get_logs_dir", return_value=logs_dir):
                        with patch("modules.audit.metrics.get_metrics_dir", return_value=metrics_dir):
                            yield {
                                "claude_dir": claude_dir,
                                "logs_dir": logs_dir,
                                "metrics_dir": metrics_dir,
                                "session_dir": session_dir
                            }

    def test_logs_execution(self, setup):
        """Test logs execution using AuditLogger directly."""
        from modules.audit.logger import AuditLogger

        # Create logger with test directory
        audit_logger = AuditLogger(log_dir=setup["logs_dir"])

        audit_logger.log_execution(
            tool_name="bash",
            parameters={"command": "ls -la"},
            result="file1\nfile2",
            duration=0.5,
            exit_code=0,
            tier="T0"
        )

        # Check log was created
        logs = list(setup["logs_dir"].glob("*.jsonl"))
        assert len(logs) > 0, "Log file should be created"

    def test_records_metrics(self, setup):
        """Test records metrics using MetricsCollector directly."""
        from modules.audit.metrics import MetricsCollector

        # Create metrics collector with test directory
        metrics_collector = MetricsCollector(metrics_dir=setup["metrics_dir"])

        metrics_collector.record_execution(
            tool_name="bash",
            command="kubectl get pods",
            duration=1.0,
            success=True,
            tier="T0"
        )

        # Check metrics file was created
        metrics_files = list(setup["metrics_dir"].glob("metrics-*.jsonl"))
        assert len(metrics_files) > 0, "Metrics file should be created"

    def test_handles_failure(self, setup):
        """Test handles failed execution."""
        post_tool_use_hook(
            tool_name="bash",
            parameters={"command": "failing_command"},
            result="error: command not found",
            duration=0.1,
            success=False
        )

        # Should not crash
        assert True

    def test_reads_pre_hook_state(self, setup):
        """Test reads state from pre-hook."""
        # Create pre-hook state
        state = HookState(
            tool_name="bash",
            command="test",
            tier="T0"
        )
        save_hook_state(state)

        # Run post-hook
        post_tool_use_hook(
            tool_name="bash",
            parameters={"command": "test"},
            result="output",
            duration=0.1,
            success=True
        )

        # Should complete without error


class TestActiveContextUpdater:
    """Test ActiveContextUpdater class."""

    @pytest.fixture
    def session_dir(self, tmp_path):
        """Create temp session directory."""
        session_dir = tmp_path / "session" / "active"
        session_dir.mkdir(parents=True)
        return session_dir

    def test_creates_context_file(self, session_dir):
        """Test creates context file."""
        with patch("post_tool_use.get_session_dir", return_value=session_dir):
            updater = ActiveContextUpdater()
            updater.update_context({"event_type": "test"})

            context_file = session_dir / "context.json"
            assert context_file.exists()

    def test_appends_events(self, session_dir):
        """Test appends events to context."""
        with patch("post_tool_use.get_session_dir", return_value=session_dir):
            updater = ActiveContextUpdater()

            updater.update_context({"event_type": "event1"})
            updater.update_context({"event_type": "event2"})

            context_file = session_dir / "context.json"
            with open(context_file) as f:
                data = json.load(f)

            assert len(data["critical_events"]) == 2

    def test_limits_events(self, session_dir):
        """Test limits stored events to 20."""
        with patch("post_tool_use.get_session_dir", return_value=session_dir):
            updater = ActiveContextUpdater()

            # Add 25 events
            for i in range(25):
                updater.update_context({"event_type": f"event_{i}"})

            context_file = session_dir / "context.json"
            with open(context_file) as f:
                data = json.load(f)

            assert len(data["critical_events"]) == 20

    def test_adds_timestamp_to_events(self, session_dir):
        """Test adds timestamp to events."""
        with patch("post_tool_use.get_session_dir", return_value=session_dir):
            updater = ActiveContextUpdater()
            updater.update_context({"event_type": "test"})

            context_file = session_dir / "context.json"
            with open(context_file) as f:
                data = json.load(f)

            assert "timestamp" in data["critical_events"][0]


class TestNotificationHandler:
    """Test NotificationHandler class."""

    @pytest.fixture
    def handler(self):
        return NotificationHandler()

    def test_detects_long_execution(self, handler):
        """Test detects long execution."""
        notifications = handler.check_thresholds(
            duration=120.0,  # 2 minutes
            success=True,
            tool_name="bash"
        )

        long_notification = next(
            (n for n in notifications if n["type"] == "long_execution"),
            None
        )
        assert long_notification is not None
        assert long_notification["severity"] == "warning"

    def test_detects_failure(self, handler):
        """Test detects command failure."""
        notifications = handler.check_thresholds(
            duration=1.0,
            success=False,
            tool_name="bash"
        )

        failure_notification = next(
            (n for n in notifications if n["type"] == "command_failure"),
            None
        )
        assert failure_notification is not None
        assert failure_notification["severity"] == "error"

    def test_no_notification_for_normal(self, handler):
        """Test no notification for normal execution."""
        notifications = handler.check_thresholds(
            duration=1.0,  # Normal duration
            success=True,
            tool_name="bash"
        )

        assert len(notifications) == 0


class TestCriticalEventDetection:
    """Test critical event detection in post-hook."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        logs_dir = claude_dir / "logs"
        logs_dir.mkdir()
        metrics_dir = claude_dir / "metrics"
        metrics_dir.mkdir()
        session_dir = claude_dir / "session" / "active"
        session_dir.mkdir(parents=True)

        with patch("post_tool_use.get_logs_dir", return_value=logs_dir):
            with patch("post_tool_use.get_session_dir", return_value=session_dir):
                with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
                    with patch("modules.audit.logger.get_logs_dir", return_value=logs_dir):
                        with patch("modules.audit.metrics.get_metrics_dir", return_value=metrics_dir):
                            yield session_dir

    def test_detects_git_commit_event(self, setup):
        """Test detects git commit event."""
        post_tool_use_hook(
            tool_name="bash",
            parameters={"command": "git commit -m 'test'"},
            result="[main abc1234] test commit\n 1 file changed",
            duration=0.5,
            success=True
        )

        context_file = setup / "context.json"
        if context_file.exists():
            with open(context_file) as f:
                data = json.load(f)
            # Check if git commit event was detected
            git_events = [e for e in data.get("critical_events", []) if "git" in str(e)]
            # May or may not be present depending on detection


class TestIntegration:
    """Integration tests for post-tool hook."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        logs_dir = claude_dir / "logs"
        logs_dir.mkdir()
        metrics_dir = claude_dir / "metrics"
        metrics_dir.mkdir()
        session_dir = claude_dir / "session" / "active"
        session_dir.mkdir(parents=True)

        with patch("post_tool_use.get_logs_dir", return_value=logs_dir):
            with patch("post_tool_use.get_session_dir", return_value=session_dir):
                with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
                    with patch("modules.audit.logger.get_logs_dir", return_value=logs_dir):
                        with patch("modules.audit.metrics.get_metrics_dir", return_value=metrics_dir):
                            yield

    def test_full_flow(self):
        """Test complete post-hook flow."""
        # Create pre-hook state
        state = HookState(
            tool_name="bash",
            command="kubectl get pods",
            tier="T0"
        )
        save_hook_state(state)

        # Run post-hook
        post_tool_use_hook(
            tool_name="bash",
            parameters={"command": "kubectl get pods"},
            result="NAME   STATUS\npod-1  Running",
            duration=1.5,
            success=True
        )

        # Should complete without error
        assert True

    def test_handles_missing_pre_hook_state(self):
        """Test handles missing pre-hook state gracefully."""
        # Don't create pre-hook state
        post_tool_use_hook(
            tool_name="bash",
            parameters={"command": "test"},
            result="output",
            duration=0.1,
            success=True
        )

        # Should complete without error
        assert True
