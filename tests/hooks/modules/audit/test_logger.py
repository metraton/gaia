#!/usr/bin/env python3
"""
Tests for Audit Logger.

Validates:
1. AuditLogger class
2. Execution logging
3. Output sanitization
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.logger import (
    AuditLogger,
    get_audit_logger,
    log_execution,
)


class TestAuditLogger:
    """Test AuditLogger class."""

    @pytest.fixture
    def log_dir(self, tmp_path):
        """Create temp log directory."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        return log_dir

    @pytest.fixture
    def logger(self, log_dir):
        """Create logger with temp directory."""
        return AuditLogger(log_dir=log_dir)

    def test_logs_execution(self, logger, log_dir):
        """Test logging execution creates file."""
        logger.log_execution(
            tool_name="bash",
            parameters={"command": "ls -la"},
            result="file1\nfile2",
            duration=0.5,
            exit_code=0,
            tier="T0"
        )

        # Check session log exists
        session_logs = list(log_dir.glob("session-*.jsonl"))
        assert len(session_logs) > 0

    def test_log_contains_expected_fields(self, logger, log_dir):
        """Test log entry contains expected fields."""
        logger.log_execution(
            tool_name="bash",
            parameters={"command": "pwd"},
            result="/home/user",
            duration=0.1,
            exit_code=0,
            tier="T0"
        )

        session_log = next(log_dir.glob("session-*.jsonl"))
        with open(session_log) as f:
            entry = json.loads(f.readline())

        assert entry["tool_name"] == "bash"
        assert entry["command"] == "pwd"
        assert entry["exit_code"] == 0
        assert entry["tier"] == "T0"
        assert "duration_ms" in entry
        assert "timestamp" in entry

    def test_logs_to_daily_audit(self, logger, log_dir):
        """Test logs to daily audit file."""
        logger.log_execution(
            tool_name="bash",
            parameters={"command": "date"},
            result="2024-01-01",
            duration=0.05,
        )

        daily_logs = list(log_dir.glob("audit-*.jsonl"))
        assert len(daily_logs) > 0


class TestHashOutput:
    """Test output hashing."""

    @pytest.fixture
    def logger(self, tmp_path):
        """Create logger."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        return AuditLogger(log_dir=log_dir)

    def test_creates_hash(self, logger):
        """Test creates hash of output."""
        result = logger.hash_output("test output")
        assert len(result) == 16  # Truncated SHA256

    def test_same_input_same_hash(self, logger):
        """Test same input produces same hash."""
        hash1 = logger.hash_output("same content")
        hash2 = logger.hash_output("same content")
        assert hash1 == hash2

    def test_different_input_different_hash(self, logger):
        """Test different input produces different hash."""
        hash1 = logger.hash_output("content 1")
        hash2 = logger.hash_output("content 2")
        assert hash1 != hash2

    def test_truncates_long_output(self, logger):
        """Test truncates long output before hashing."""
        long_output = "x" * 10000
        # Should not crash
        result = logger.hash_output(long_output, max_length=100)
        assert len(result) == 16


class TestSanitizeParams:
    """Test parameter sanitization."""

    @pytest.fixture
    def logger(self, tmp_path):
        """Create logger."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        return AuditLogger(log_dir=log_dir)

    def test_redacts_passwords(self, logger):
        """Test redacts password fields."""
        params = {"command": "test", "password": "secret123"}
        sanitized = logger._sanitize_params(params)
        assert sanitized["password"] == "[REDACTED]"

    def test_redacts_tokens(self, logger):
        """Test redacts token fields."""
        params = {"api_token": "abc123", "command": "test"}
        sanitized = logger._sanitize_params(params)
        assert sanitized["api_token"] == "[REDACTED]"

    def test_redacts_secrets(self, logger):
        """Test redacts secret fields."""
        params = {"secret_key": "xyz789", "other": "value"}
        sanitized = logger._sanitize_params(params)
        assert sanitized["secret_key"] == "[REDACTED]"

    def test_truncates_long_values(self, logger):
        """Test truncates long values."""
        long_value = "x" * 1000
        params = {"long_field": long_value}
        sanitized = logger._sanitize_params(params)
        assert len(sanitized["long_field"]) < len(long_value)
        assert "truncated" in sanitized["long_field"]

    def test_preserves_normal_values(self, logger):
        """Test preserves normal values."""
        params = {"command": "ls -la", "timeout": 30}
        sanitized = logger._sanitize_params(params)
        assert sanitized["command"] == "ls -la"
        assert sanitized["timeout"] == 30


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        with patch("modules.audit.logger.get_logs_dir", return_value=log_dir):
            # Reset global logger
            import modules.audit.logger as logger_module
            logger_module._audit_logger = None
            yield log_dir

    def test_get_audit_logger(self):
        """Test get_audit_logger returns instance."""
        logger = get_audit_logger()
        assert isinstance(logger, AuditLogger)

    def test_get_audit_logger_is_singleton(self):
        """Test get_audit_logger returns same instance."""
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is logger2

    def test_log_execution_function(self, setup):
        """Test log_execution convenience function."""
        log_execution(
            tool_name="bash",
            parameters={"command": "whoami"},
            result="user",
            duration=0.1
        )

        # Check log was created
        logs = list(setup.glob("*.jsonl"))
        assert len(logs) > 0


class TestOutputPreview:
    """Test output preview generation."""

    @pytest.fixture
    def logger(self, tmp_path):
        """Create logger."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        return AuditLogger(log_dir=log_dir)

    def test_short_output_not_truncated(self, logger, tmp_path):
        """Test short output is not truncated."""
        log_dir = tmp_path / "logs"
        logger.log_execution(
            tool_name="bash",
            parameters={},
            result="short output",
            duration=0.1
        )

        session_log = next(log_dir.glob("session-*.jsonl"))
        with open(session_log) as f:
            entry = json.loads(f.readline())

        assert entry["output_preview"] == "short output"

    def test_long_output_truncated(self, logger, tmp_path):
        """Test long output is truncated in preview."""
        log_dir = tmp_path / "logs"
        long_output = "x" * 500
        logger.log_execution(
            tool_name="bash",
            parameters={},
            result=long_output,
            duration=0.1
        )

        session_log = next(log_dir.glob("session-*.jsonl"))
        with open(session_log) as f:
            entry = json.loads(f.readline())

        assert len(entry["output_preview"]) < len(long_output)
        assert "..." in entry["output_preview"]
