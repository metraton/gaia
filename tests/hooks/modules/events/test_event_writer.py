#!/usr/bin/env python3
"""
Tests for Event Writer module.

Validates:
1. write_event creates valid JSONL
2. read_events filters by hours and type
3. cleanup_old_events removes old entries
4. Event type constants exist
5. Concurrent writes don't corrupt file
6. Empty/missing events file returns empty list
"""

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.events.event_writer import (
    AGENT_COMPLETE,
    AGENT_DISPATCH,
    COMMAND_EXECUTED,
    HEARTBEAT,
    SESSION_END,
    TRIGGER_SCHEDULED,
    USER_NOTE,
    EventWriter,
    cleanup_old_events,
    read_events,
)


class TestEventTypeConstants:
    """Test event type constants are defined correctly."""

    def test_agent_dispatch_constant(self):
        assert AGENT_DISPATCH == "agent.dispatch"

    def test_agent_complete_constant(self):
        assert AGENT_COMPLETE == "agent.complete"

    def test_command_executed_constant(self):
        assert COMMAND_EXECUTED == "command.executed"

    def test_session_end_constant(self):
        assert SESSION_END == "session.end"

    def test_trigger_scheduled_constant(self):
        assert TRIGGER_SCHEDULED == "trigger.scheduled"

    def test_heartbeat_constant(self):
        assert HEARTBEAT == "heartbeat"

    def test_user_note_constant(self):
        assert USER_NOTE == "user.note"


class TestEventWriter:
    """Test EventWriter.write_event creates valid JSONL."""

    @pytest.fixture
    def events_dir(self, tmp_path):
        edir = tmp_path / "events"
        edir.mkdir()
        return edir

    @pytest.fixture
    def writer(self, events_dir):
        return EventWriter(events_dir=events_dir)

    def test_write_event_creates_jsonl(self, writer, events_dir):
        """write_event should create a valid JSONL line."""
        writer.write_event(
            AGENT_DISPATCH, "hook", "terraform-architect",
            "dispatched for: plan staging",
        )
        events_file = events_dir / "events.jsonl"
        assert events_file.exists()

        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["type"] == "agent.dispatch"
        assert record["source"] == "hook"
        assert record["agent"] == "terraform-architect"
        assert record["result"] == "dispatched for: plan staging"
        assert record["severity"] == "info"
        assert "ts" in record

    def test_write_event_with_meta(self, writer, events_dir):
        """write_event should include meta when provided."""
        writer.write_event(
            AGENT_COMPLETE, "hook", "cloud-troubleshooter",
            "COMPLETE",
            meta={"episode_id": "ep_123", "summary": "found issue"},
        )
        events_file = events_dir / "events.jsonl"
        record = json.loads(events_file.read_text().strip())
        assert record["meta"]["episode_id"] == "ep_123"
        assert record["meta"]["summary"] == "found issue"

    def test_write_event_custom_severity(self, writer, events_dir):
        """write_event should respect custom severity."""
        writer.write_event(
            COMMAND_EXECUTED, "hook", "",
            "error: kubectl apply failed",
            severity="warning",
        )
        events_file = events_dir / "events.jsonl"
        record = json.loads(events_file.read_text().strip())
        assert record["severity"] == "warning"

    def test_write_event_no_meta_key(self, writer, events_dir):
        """write_event without meta should not include meta key."""
        writer.write_event(
            SESSION_END, "hook", "", "session ended: user_exit",
        )
        events_file = events_dir / "events.jsonl"
        record = json.loads(events_file.read_text().strip())
        assert "meta" not in record

    def test_write_multiple_events(self, writer, events_dir):
        """Multiple writes should produce multiple JSONL lines."""
        writer.write_event(AGENT_DISPATCH, "hook", "agent1", "dispatched")
        writer.write_event(AGENT_COMPLETE, "hook", "agent1", "COMPLETE")
        writer.write_event(SESSION_END, "hook", "", "ended")

        events_file = events_dir / "events.jsonl"
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_write_event_timestamp_is_utc_iso(self, writer, events_dir):
        """Timestamps should be valid UTC ISO 8601."""
        writer.write_event(HEARTBEAT, "system", "", "ok")
        events_file = events_dir / "events.jsonl"
        record = json.loads(events_file.read_text().strip())
        ts = datetime.fromisoformat(record["ts"])
        assert ts.tzinfo is not None  # timezone-aware

    def test_write_event_creates_dir_if_missing(self, tmp_path):
        """Writer should create events dir if it doesn't exist."""
        edir = tmp_path / "nonexistent" / "events"
        writer = EventWriter(events_dir=edir)
        writer.write_event(HEARTBEAT, "test", "", "ok")
        assert (edir / "events.jsonl").exists()

    def test_write_event_fails_silently(self, tmp_path):
        """Writer should not raise on failure."""
        # Use a file path as dir to force an error
        bad_dir = tmp_path / "bad"
        bad_dir.write_text("not a directory")
        writer = EventWriter(events_dir=bad_dir)
        # Should not raise
        writer.write_event(HEARTBEAT, "test", "", "ok")


class TestReadEvents:
    """Test read_events filters by hours and type."""

    @pytest.fixture
    def events_dir(self, tmp_path):
        edir = tmp_path / "events"
        edir.mkdir()
        return edir

    def _write_raw_event(self, events_dir, event_type, ts, result="ok", agent=""):
        """Write a raw JSONL event with a specific timestamp."""
        events_file = events_dir / "events.jsonl"
        record = {
            "ts": ts.isoformat(),
            "type": event_type,
            "source": "test",
            "agent": agent,
            "result": result,
            "severity": "info",
        }
        with open(events_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def test_read_recent_events(self, events_dir):
        """read_events should return events within the time window."""
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, AGENT_DISPATCH, now - timedelta(hours=1))
        self._write_raw_event(events_dir, AGENT_COMPLETE, now - timedelta(minutes=30))

        results = read_events(hours=24, events_dir=events_dir)
        assert len(results) == 2

    def test_read_events_filters_old(self, events_dir):
        """read_events should exclude events older than the time window."""
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, AGENT_DISPATCH, now - timedelta(hours=48))
        self._write_raw_event(events_dir, AGENT_COMPLETE, now - timedelta(minutes=30))

        results = read_events(hours=24, events_dir=events_dir)
        assert len(results) == 1
        assert results[0]["type"] == AGENT_COMPLETE

    def test_read_events_filters_by_type(self, events_dir):
        """read_events should filter by event_type when specified."""
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, AGENT_DISPATCH, now - timedelta(hours=1))
        self._write_raw_event(events_dir, AGENT_COMPLETE, now - timedelta(minutes=30))
        self._write_raw_event(events_dir, HEARTBEAT, now - timedelta(minutes=10))

        results = read_events(hours=24, event_type=HEARTBEAT, events_dir=events_dir)
        assert len(results) == 1
        assert results[0]["type"] == HEARTBEAT

    def test_read_events_respects_limit(self, events_dir):
        """read_events should cap at limit."""
        now = datetime.now(timezone.utc)
        for i in range(10):
            self._write_raw_event(events_dir, HEARTBEAT, now - timedelta(minutes=i))

        results = read_events(hours=24, limit=3, events_dir=events_dir)
        assert len(results) == 3

    def test_read_events_empty_file(self, events_dir):
        """read_events on empty file should return empty list."""
        (events_dir / "events.jsonl").write_text("")
        results = read_events(events_dir=events_dir)
        assert results == []

    def test_read_events_missing_file(self, events_dir):
        """read_events with no events.jsonl should return empty list."""
        results = read_events(events_dir=events_dir)
        assert results == []

    def test_read_events_skips_malformed_lines(self, events_dir):
        """read_events should skip lines that are not valid JSON."""
        now = datetime.now(timezone.utc)
        events_file = events_dir / "events.jsonl"
        self._write_raw_event(events_dir, HEARTBEAT, now - timedelta(minutes=5))
        with open(events_file, "a") as f:
            f.write("this is not json\n")
        self._write_raw_event(events_dir, AGENT_DISPATCH, now - timedelta(minutes=2))

        results = read_events(hours=24, events_dir=events_dir)
        assert len(results) == 2


class TestCleanupOldEvents:
    """Test cleanup_old_events removes old entries."""

    @pytest.fixture
    def events_dir(self, tmp_path):
        edir = tmp_path / "events"
        edir.mkdir()
        return edir

    def _write_raw_event(self, events_dir, ts, event_type="heartbeat"):
        events_file = events_dir / "events.jsonl"
        record = {
            "ts": ts.isoformat(),
            "type": event_type,
            "source": "test",
            "agent": "",
            "result": "ok",
            "severity": "info",
        }
        with open(events_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def test_cleanup_removes_old_events(self, events_dir):
        """cleanup_old_events should remove events beyond retention window."""
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, now - timedelta(days=10))
        self._write_raw_event(events_dir, now - timedelta(days=8))
        self._write_raw_event(events_dir, now - timedelta(days=1))

        removed = cleanup_old_events(days=7, events_dir=events_dir)
        assert removed == 2

        # Verify remaining
        events_file = events_dir / "events.jsonl"
        lines = [l for l in events_file.read_text().strip().split("\n") if l]
        assert len(lines) == 1

    def test_cleanup_keeps_recent_events(self, events_dir):
        """cleanup_old_events should keep events within retention window."""
        now = datetime.now(timezone.utc)
        self._write_raw_event(events_dir, now - timedelta(days=1))
        self._write_raw_event(events_dir, now - timedelta(hours=6))

        removed = cleanup_old_events(days=7, events_dir=events_dir)
        assert removed == 0

    def test_cleanup_missing_file(self, events_dir):
        """cleanup_old_events with no file should return 0."""
        removed = cleanup_old_events(events_dir=events_dir)
        assert removed == 0

    def test_cleanup_preserves_unparseable_lines(self, events_dir):
        """cleanup_old_events should keep lines it cannot parse."""
        now = datetime.now(timezone.utc)
        events_file = events_dir / "events.jsonl"
        self._write_raw_event(events_dir, now - timedelta(days=10))
        with open(events_file, "a") as f:
            f.write("unparseable line\n")
        self._write_raw_event(events_dir, now - timedelta(hours=1))

        removed = cleanup_old_events(days=7, events_dir=events_dir)
        assert removed == 1

        lines = [l for l in events_file.read_text().strip().split("\n") if l]
        assert len(lines) == 2  # unparseable + recent


class TestConcurrentWrites:
    """Test that concurrent writes don't corrupt the events file."""

    @pytest.fixture
    def events_dir(self, tmp_path):
        edir = tmp_path / "events"
        edir.mkdir()
        return edir

    def test_concurrent_writes(self, events_dir):
        """Multiple threads writing events should produce valid JSONL."""
        num_threads = 5
        events_per_thread = 10
        errors = []

        def write_events(thread_id):
            try:
                writer = EventWriter(events_dir=events_dir)
                for i in range(events_per_thread):
                    writer.write_event(
                        HEARTBEAT, "test",
                        f"thread-{thread_id}",
                        f"event-{i}",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = []
        for tid in range(num_threads):
            t = threading.Thread(target=write_events, args=(tid,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        # Verify all events are valid JSONL
        events_file = events_dir / "events.jsonl"
        lines = [l for l in events_file.read_text().strip().split("\n") if l]
        assert len(lines) == num_threads * events_per_thread

        for line in lines:
            record = json.loads(line)
            assert "ts" in record
            assert "type" in record
            assert record["type"] == HEARTBEAT
