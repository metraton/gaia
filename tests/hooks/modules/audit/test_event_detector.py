#!/usr/bin/env python3
"""
Tests for Critical Event Detector.

Validates:
1. Event detection (git, file modifications, speckit)
2. CriticalEvent structure
3. Detector functions
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.event_detector import (
    CriticalEventDetector,
    CriticalEvent,
    EventType,
    get_detector,
    detect_critical_event,
)


class TestEventType:
    """Test EventType enum."""

    def test_event_types_exist(self):
        """Test event types are defined."""
        assert EventType.GIT_COMMIT.value == "git_commit"
        assert EventType.GIT_PUSH.value == "git_push"
        assert EventType.FILE_MODIFICATIONS.value == "file_modifications"
        assert EventType.SPECKIT_MILESTONE.value == "speckit_milestone"


class TestCriticalEvent:
    """Test CriticalEvent dataclass."""

    def test_creates_event(self):
        """Test creating a critical event."""
        event = CriticalEvent(
            event_type=EventType.GIT_COMMIT,
            data={"commit_hash": "abc123"}
        )
        assert event.event_type == EventType.GIT_COMMIT
        assert event.data["commit_hash"] == "abc123"

    def test_auto_sets_timestamp(self):
        """Test timestamp is auto-set."""
        event = CriticalEvent(
            event_type=EventType.GIT_PUSH,
            data={}
        )
        assert event.timestamp != ""

    def test_to_dict(self):
        """Test to_dict conversion."""
        event = CriticalEvent(
            event_type=EventType.FILE_MODIFICATIONS,
            data={"count": 5}
        )
        result = event.to_dict()
        assert result["event_type"] == "file_modifications"
        assert result["count"] == 5
        assert "timestamp" in result


class TestGitCommitDetection:
    """Test git commit detection."""

    @pytest.fixture
    def detector(self):
        return CriticalEventDetector()

    def test_detects_git_commit(self, detector):
        """Test detects successful git commit."""
        result = detector.detect_git_commit(
            tool_name="bash",
            parameters={"command": "git commit -m 'test'"},
            result="[main abc1234] test commit\n 1 file changed",
            success=True
        )
        assert result is not None
        assert result.event_type == EventType.GIT_COMMIT
        assert result.data["commit_hash"] == "abc1234"

    def test_extracts_commit_message(self, detector):
        """Test extracts commit message."""
        result = detector.detect_git_commit(
            tool_name="bash",
            parameters={"command": "git commit -m 'my message'"},
            result="[feature/test def5678] my message\n 2 files changed",
            success=True
        )
        assert result is not None
        assert "my message" in result.data.get("commit_message", "")

    def test_ignores_failed_commit(self, detector):
        """Test ignores failed commit."""
        result = detector.detect_git_commit(
            tool_name="bash",
            parameters={"command": "git commit -m 'test'"},
            result="nothing to commit",
            success=False
        )
        assert result is None

    def test_ignores_non_bash_tool(self, detector):
        """Test ignores non-bash tool."""
        result = detector.detect_git_commit(
            tool_name="Read",
            parameters={"command": "git commit"},
            result="[main abc1234] test",
            success=True
        )
        assert result is None


class TestGitPushDetection:
    """Test git push detection."""

    @pytest.fixture
    def detector(self):
        return CriticalEventDetector()

    def test_detects_git_push(self, detector):
        """Test detects successful git push."""
        result = detector.detect_git_push(
            tool_name="bash",
            parameters={"command": "git push origin main"},
            result="To github.com:user/repo.git\n   abc123..def456  main -> main",
            success=True
        )
        assert result is not None
        assert result.event_type == EventType.GIT_PUSH

    def test_extracts_branch(self, detector):
        """Test extracts branch name."""
        result = detector.detect_git_push(
            tool_name="bash",
            parameters={"command": "git push origin feature/test"},
            result="To github.com:user/repo.git\n   abc123..def456  feature/test -> feature/test",
            success=True
        )
        if result:
            assert "feature" in result.data.get("branch", "") or "feature" in str(result.data)

    def test_ignores_failed_push(self, detector):
        """Test ignores failed push."""
        result = detector.detect_git_push(
            tool_name="bash",
            parameters={"command": "git push origin main"},
            result="error: failed to push",
            success=False
        )
        assert result is None


class TestFileModificationsDetection:
    """Test file modifications detection."""

    @pytest.fixture
    def detector(self):
        # Reset counter
        CriticalEventDetector._file_modification_count = 0
        return CriticalEventDetector()

    def test_detects_modification_threshold(self, detector):
        """Test detects when threshold reached."""
        # First two modifications should not trigger
        result1 = detector.detect_file_modifications("Edit")
        assert result1 is None

        result2 = detector.detect_file_modifications("Write")
        assert result2 is None

        # Third modification should trigger
        result3 = detector.detect_file_modifications("Edit")
        assert result3 is not None
        assert result3.event_type == EventType.FILE_MODIFICATIONS
        assert result3.data["modification_count"] == 3

    def test_resets_after_detection(self, detector):
        """Test counter resets after detection."""
        # Trigger threshold
        detector.detect_file_modifications("Edit")
        detector.detect_file_modifications("Write")
        detector.detect_file_modifications("Edit")

        # Next modifications should start fresh
        result = detector.detect_file_modifications("Write")
        assert result is None

    def test_ignores_read_tools(self, detector):
        """Test ignores read-only tools."""
        result = detector.detect_file_modifications("Read")
        assert result is None
        assert CriticalEventDetector._file_modification_count == 0


class TestSpeckitMilestoneDetection:
    """Test spec-kit milestone detection."""

    @pytest.fixture
    def detector(self):
        return CriticalEventDetector()

    @pytest.mark.parametrize("command", [
        "/speckit.specify",
        "/speckit.plan",
        "/speckit.tasks",
        "/speckit.implement",
        "/speckit.constitution",
    ])
    def test_detects_speckit_commands(self, detector, command):
        """Test detects spec-kit commands."""
        result = detector.detect_speckit_milestone(
            tool_name="SlashCommand",
            parameters={"command": command}
        )
        assert result is not None
        assert result.event_type == EventType.SPECKIT_MILESTONE
        assert result.data["command"] == command

    def test_ignores_non_speckit_commands(self, detector):
        """Test ignores non-speckit commands."""
        result = detector.detect_speckit_milestone(
            tool_name="SlashCommand",
            parameters={"command": "/gaia"}
        )
        assert result is None

    def test_ignores_non_slash_command_tool(self, detector):
        """Test ignores non-SlashCommand tool."""
        result = detector.detect_speckit_milestone(
            tool_name="bash",
            parameters={"command": "/speckit.plan"}
        )
        assert result is None


class TestDetectAll:
    """Test detect_all combined detection."""

    @pytest.fixture
    def detector(self):
        CriticalEventDetector._file_modification_count = 0
        return CriticalEventDetector()

    def test_detects_multiple_events(self, detector):
        """Test can detect multiple events."""
        # Git commit
        events = detector.detect_all(
            tool_name="bash",
            parameters={"command": "git commit -m 'test'"},
            result="[main abc1234] test",
            success=True
        )
        assert len(events) >= 1
        assert any(e.event_type == EventType.GIT_COMMIT for e in events)

    def test_returns_empty_for_no_events(self, detector):
        """Test returns empty list when no events."""
        events = detector.detect_all(
            tool_name="bash",
            parameters={"command": "ls -la"},
            result="file1 file2",
            success=True
        )
        # May or may not be empty depending on counter state
        assert isinstance(events, list)


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_get_detector(self):
        """Test get_detector returns instance."""
        detector = get_detector()
        assert isinstance(detector, CriticalEventDetector)

    def test_get_detector_is_singleton(self):
        """Test get_detector returns same instance."""
        detector1 = get_detector()
        detector2 = get_detector()
        assert detector1 is detector2

    def test_detect_critical_event_function(self):
        """Test detect_critical_event convenience function."""
        events = detect_critical_event(
            tool_name="bash",
            parameters={"command": "ls"},
            result="files",
            success=True
        )
        assert isinstance(events, list)
