#!/usr/bin/env python3
"""Tests for context_injector.consume_anomaly_flag() TTL behavior.

Validates:
- Flag is consumed when fresh (within TTL)
- Flag is NOT consumed when expired (past TTL)
- Flag file doesn't exist
- Edge cases around TTL boundary
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.context.context_injector import consume_anomaly_flag


# ============================================================================
# HELPERS
# ============================================================================

_FLAG_RELATIVE = Path(
    ".claude/project-context/workflow-episodic-memory/signals/needs_analysis.flag"
)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test inside a fresh tmp_path so the flag path resolves there."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _write_flag(tmp_path: Path, data: dict) -> Path:
    """Write the anomaly flag JSON at the expected relative path."""
    flag_path = tmp_path / _FLAG_RELATIVE
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(json.dumps(data))
    return flag_path


def _fresh_flag(anomalies=None, ttl_hours=1) -> dict:
    """Build a flag payload with created_at = now (fresh, within TTL)."""
    return {
        "created_at": datetime.now().isoformat(),
        "ttl_hours": ttl_hours,
        "anomalies": anomalies or [{"message": "Unexpected T3 without approval"}],
    }


def _expired_flag(anomalies=None, ttl_hours=1, expired_by_hours=2) -> dict:
    """Build a flag payload with created_at in the past (outside TTL)."""
    created = datetime.now() - timedelta(hours=ttl_hours + expired_by_hours)
    return {
        "created_at": created.isoformat(),
        "ttl_hours": ttl_hours,
        "anomalies": anomalies or [{"message": "Stale anomaly"}],
    }


# ============================================================================
# FLAG CONSUMED WHEN FRESH
# ============================================================================

class TestFreshFlagConsumed:
    """A fresh flag (within TTL) should append a warning and delete the file."""

    def test_fresh_flag_appends_anomaly_warning(self, tmp_path):
        flag_path = _write_flag(tmp_path, _fresh_flag())

        result = consume_anomaly_flag("base prompt")

        assert "Anomaly Alert" in result
        assert "Unexpected T3 without approval" in result
        assert not flag_path.exists(), "Flag should be deleted after consumption"

    def test_fresh_flag_preserves_original_prompt(self, tmp_path):
        _write_flag(tmp_path, _fresh_flag())

        result = consume_anomaly_flag("original context here")

        assert result.startswith("original context here")

    def test_multiple_anomalies_are_joined(self, tmp_path):
        anomalies = [
            {"message": "High error rate"},
            {"message": "Missing service account"},
        ]
        _write_flag(tmp_path, _fresh_flag(anomalies=anomalies))

        result = consume_anomaly_flag("prompt")

        assert "High error rate" in result
        assert "Missing service account" in result

    def test_custom_short_ttl_still_fresh(self, tmp_path):
        """A 5-minute TTL flag created now is still fresh."""
        data = {
            "created_at": datetime.now().isoformat(),
            "ttl_hours": 0.0833,  # ~5 minutes
            "anomalies": [{"message": "Short-lived signal"}],
        }
        flag_path = _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert "Short-lived signal" in result
        assert not flag_path.exists()


# ============================================================================
# FLAG NOT CONSUMED WHEN EXPIRED
# ============================================================================

class TestExpiredFlagNotConsumed:
    """An expired flag (past TTL) should be auto-deleted without appending a warning."""

    def test_expired_flag_does_not_append_warning(self, tmp_path):
        flag_path = _write_flag(tmp_path, _expired_flag())

        result = consume_anomaly_flag("base prompt")

        assert result == "base prompt"
        assert not flag_path.exists(), "Expired flag should still be deleted"

    def test_expired_flag_with_custom_ttl(self, tmp_path):
        """A flag with 2h TTL created 5h ago is expired."""
        data = _expired_flag(ttl_hours=2, expired_by_hours=3)
        flag_path = _write_flag(tmp_path, data)

        result = consume_anomaly_flag("unchanged prompt")

        assert result == "unchanged prompt"
        assert not flag_path.exists()

    def test_expired_flag_fallback_to_mtime(self, tmp_path):
        """When created_at is missing, mtime is used for TTL check."""
        data = {
            "ttl_hours": 0.0001,  # ~0.36 seconds -- will be expired by test time
            "anomalies": [{"message": "Should not appear"}],
        }
        flag_path = _write_flag(tmp_path, data)
        # Set mtime to 2 hours ago
        import os
        old_time = (datetime.now() - timedelta(hours=2)).timestamp()
        os.utime(flag_path, (old_time, old_time))

        result = consume_anomaly_flag("base")

        assert result == "base"
        assert not flag_path.exists()


# ============================================================================
# FLAG FILE DOES NOT EXIST
# ============================================================================

class TestFlagNotPresent:
    """When no flag file exists, consume_anomaly_flag is a no-op."""

    def test_no_flag_returns_prompt_unchanged(self):
        result = consume_anomaly_flag("untouched prompt")
        assert result == "untouched prompt"

    def test_no_flag_with_empty_prompt(self):
        result = consume_anomaly_flag("")
        assert result == ""


# ============================================================================
# EDGE CASES AROUND TTL BOUNDARY
# ============================================================================

class TestTTLBoundary:
    """Behavior at and near the TTL boundary."""

    def test_flag_exactly_at_ttl_boundary_is_fresh(self, tmp_path):
        """A flag whose age equals TTL minus 1 second should still be consumed."""
        almost_expired = datetime.now() - timedelta(hours=1) + timedelta(seconds=5)
        data = {
            "created_at": almost_expired.isoformat(),
            "ttl_hours": 1,
            "anomalies": [{"message": "Boundary signal"}],
        }
        _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert "Boundary signal" in result

    def test_flag_just_past_ttl_is_expired(self, tmp_path):
        """A flag 1 second past TTL should be expired."""
        just_past = datetime.now() - timedelta(hours=1, seconds=2)
        data = {
            "created_at": just_past.isoformat(),
            "ttl_hours": 1,
            "anomalies": [{"message": "Should not appear"}],
        }
        flag_path = _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert result == "base"
        assert not flag_path.exists()

    def test_malformed_flag_json_is_handled_gracefully(self, tmp_path):
        """If the flag file contains invalid JSON, consume silently skips."""
        flag_path = tmp_path / _FLAG_RELATIVE
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text("{not valid json!!")

        result = consume_anomaly_flag("safe prompt")

        assert result == "safe prompt"

    def test_flag_with_no_anomalies_list(self, tmp_path):
        """Flag exists but has empty anomalies -- consumed, no warning text appended."""
        data = {
            "created_at": datetime.now().isoformat(),
            "ttl_hours": 1,
            "anomalies": [],
        }
        flag_path = _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert "Anomaly Alert" not in result
        assert not flag_path.exists()

    def test_flag_with_anomaly_missing_message_key(self, tmp_path):
        """Anomaly entry without 'message' key should be skipped in summary."""
        data = {
            "created_at": datetime.now().isoformat(),
            "ttl_hours": 1,
            "anomalies": [{"severity": "high"}, {"message": "Real warning"}],
        }
        _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert "Real warning" in result

    def test_flag_with_timestamp_field_instead_of_created_at(self, tmp_path):
        """Falls back to 'timestamp' when 'created_at' is missing."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "ttl_hours": 1,
            "anomalies": [{"message": "Via timestamp field"}],
        }
        flag_path = _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert "Via timestamp field" in result
        assert not flag_path.exists()

    def test_default_ttl_is_one_hour(self, tmp_path):
        """When ttl_hours is absent, default is 1 hour."""
        created_30_min_ago = datetime.now() - timedelta(minutes=30)
        data = {
            "created_at": created_30_min_ago.isoformat(),
            "anomalies": [{"message": "Default TTL signal"}],
        }
        _write_flag(tmp_path, data)

        result = consume_anomaly_flag("base")

        assert "Default TTL signal" in result
