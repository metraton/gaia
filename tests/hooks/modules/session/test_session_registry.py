#!/usr/bin/env python3
"""
Tests for Session Registry Module — heartbeat-only liveness.

Covers:
1. register_session / unregister_session / is_session_alive
2. touch_session — refreshes last_heartbeat with 30s throttle
3. get_live_sessions — returns sessions within HEARTBEAT_TTL_SECONDS;
   include_headless=False excludes headless sessions
4. cleanup_stale_entries — removes entries whose heartbeat is older
   than the grace window; sweeps legacy/junk entries
5. Robustness against corrupt files and concurrent writes
6. Legacy schema compat — entries without last_heartbeat are dead

The old PID-tracking model (pid + pid_create_time) is gone: the hook
process is ephemeral, so the persisted PID was always dead by the time
another hook tried to read it. Heartbeat freshness is the only liveness
signal hooks can actually produce.
"""

import json
import os
import sys
import threading
import time
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_registry
from modules.session.session_registry import (
    HEARTBEAT_TTL_SECONDS,
    SessionRegistryError,
    cleanup_stale_entries,
    get_live_sessions,
    is_session_alive,
    register_session,
    touch_session,
    unregister_session,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Redirect the registry path to a tmp file for each test."""
    registry_file = tmp_path / "session_registry.json"
    monkeypatch.setattr(
        session_registry,
        "_get_registry_path",
        lambda: registry_file,
    )
    yield registry_file


# ---------------------------------------------------------------------------
# register_session
# ---------------------------------------------------------------------------

class TestRegisterSession:
    """register_session() persists the new heartbeat-only schema."""

    def test_registers_new_session_with_heartbeat(self, isolated_registry):
        before = time.time()
        register_session("sid-1")
        after = time.time()

        data = json.loads(isolated_registry.read_text())
        assert "sid-1" in data["sessions"]
        entry = data["sessions"]["sid-1"]
        assert entry["started_at"] is not None
        assert entry["is_headless"] is False
        assert before <= entry["last_heartbeat"] <= after

    def test_register_marks_headless_when_flag_set(self, isolated_registry):
        register_session("sid-headless", is_headless=True)
        data = json.loads(isolated_registry.read_text())
        assert data["sessions"]["sid-headless"]["is_headless"] is True

    def test_register_with_explicit_started_at(self, isolated_registry):
        register_session("sid-3", started_at="2026-04-18T00:00:00+00:00")
        data = json.loads(isolated_registry.read_text())
        assert data["sessions"]["sid-3"]["started_at"] == "2026-04-18T00:00:00+00:00"

    def test_register_updates_existing_entry(self, isolated_registry):
        register_session("sid-4", is_headless=False)
        register_session("sid-4", is_headless=True)
        data = json.loads(isolated_registry.read_text())
        assert data["sessions"]["sid-4"]["is_headless"] is True

    def test_register_empty_session_id_raises(self, isolated_registry):
        with pytest.raises(SessionRegistryError):
            register_session("")

    def test_register_creates_parent_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "dir" / "registry.json"
        monkeypatch.setattr(session_registry, "_get_registry_path", lambda: nested)
        register_session("sid-nested")
        assert nested.exists()


# ---------------------------------------------------------------------------
# unregister_session
# ---------------------------------------------------------------------------

class TestUnregisterSession:
    def test_unregisters_existing_session(self, isolated_registry):
        register_session("sid-a")
        register_session("sid-b")
        unregister_session("sid-a")
        data = json.loads(isolated_registry.read_text())
        assert "sid-a" not in data["sessions"]
        assert "sid-b" in data["sessions"]

    def test_unregister_unknown_session_is_noop(self, isolated_registry):
        register_session("sid-x")
        unregister_session("sid-nonexistent")
        data = json.loads(isolated_registry.read_text())
        assert "sid-x" in data["sessions"]

    def test_unregister_when_file_missing_is_noop(self, isolated_registry):
        assert not isolated_registry.exists()
        unregister_session("sid-none")  # must not raise

    def test_unregister_empty_session_id_is_noop(self, isolated_registry):
        register_session("sid-keep")
        unregister_session("")
        data = json.loads(isolated_registry.read_text())
        assert "sid-keep" in data["sessions"]


# ---------------------------------------------------------------------------
# is_session_alive
# ---------------------------------------------------------------------------

class TestIsSessionAlive:
    def test_returns_true_for_registered_session(self, isolated_registry):
        register_session("sid-live")
        assert is_session_alive("sid-live") is True

    def test_returns_false_for_unregistered_session(self, isolated_registry):
        register_session("sid-other")
        assert is_session_alive("sid-missing") is False

    def test_returns_false_for_empty_session_id(self, isolated_registry):
        assert is_session_alive("") is False

    def test_returns_false_when_registry_missing(self, isolated_registry):
        assert not isolated_registry.exists()
        assert is_session_alive("anything") is False


# ---------------------------------------------------------------------------
# touch_session — heartbeat refresh with 30s throttle
# ---------------------------------------------------------------------------

class TestTouchSession:
    """touch_session refreshes last_heartbeat, throttled to 30s."""

    def test_touch_refreshes_heartbeat_when_stale(self, isolated_registry):
        register_session("sid-touch")
        # Force heartbeat back >30s so the throttle won't suppress.
        data = json.loads(isolated_registry.read_text())
        old_hb = time.time() - 120
        data["sessions"]["sid-touch"]["last_heartbeat"] = old_hb
        isolated_registry.write_text(json.dumps(data))

        touch_session("sid-touch")

        new = json.loads(isolated_registry.read_text())
        assert new["sessions"]["sid-touch"]["last_heartbeat"] > old_hb

    def test_touch_is_throttled_within_30s(self, isolated_registry):
        """Two touches within the throttle window leave heartbeat unchanged."""
        register_session("sid-throttle")
        data = json.loads(isolated_registry.read_text())
        original_hb = data["sessions"]["sid-throttle"]["last_heartbeat"]

        # First touch was the register; immediate second touch should no-op.
        touch_session("sid-throttle")

        after = json.loads(isolated_registry.read_text())
        assert after["sessions"]["sid-throttle"]["last_heartbeat"] == original_hb

    def test_touch_unknown_session_is_noop(self, isolated_registry):
        """touch_session must NOT create an entry — register_session owns that.

        Resurrecting a session that should have been cleaned up would defeat
        the purpose of heartbeat-based liveness.
        """
        touch_session("sid-never-registered")
        # Registry file may not even exist yet; if it does, it must be empty.
        if isolated_registry.exists():
            data = json.loads(isolated_registry.read_text())
            assert "sid-never-registered" not in data["sessions"]

    def test_touch_empty_session_id_is_noop(self, isolated_registry):
        touch_session("")  # must not raise, must not write

    def test_touch_swallows_io_errors(self, isolated_registry, monkeypatch):
        """A registry I/O failure inside touch_session must not propagate.

        The heartbeat is a best-effort liveness signal — never break the
        calling hook because of it.
        """
        register_session("sid-io")

        def _boom(_data):
            raise SessionRegistryError("simulated I/O failure")

        # Force heartbeat back so throttle won't no-op the call.
        data = json.loads(isolated_registry.read_text())
        data["sessions"]["sid-io"]["last_heartbeat"] = time.time() - 120
        isolated_registry.write_text(json.dumps(data))

        monkeypatch.setattr(session_registry, "_save_registry", _boom)
        # Must not raise.
        touch_session("sid-io")


# ---------------------------------------------------------------------------
# get_live_sessions — heartbeat freshness + headless filter
# ---------------------------------------------------------------------------

class TestGetLiveSessions:
    def test_returns_empty_set_when_file_missing(self, isolated_registry):
        assert get_live_sessions() == set()

    def test_returns_sessions_with_fresh_heartbeat(self, isolated_registry):
        register_session("sid-1")
        register_session("sid-2")
        register_session("sid-3")
        assert get_live_sessions() == {"sid-1", "sid-2", "sid-3"}

    def test_filters_sessions_past_ttl(self, isolated_registry):
        """A session whose heartbeat is older than HEARTBEAT_TTL_SECONDS is dead."""
        register_session("sid-fresh")
        register_session("sid-stale")

        data = json.loads(isolated_registry.read_text())
        data["sessions"]["sid-stale"]["last_heartbeat"] = (
            time.time() - HEARTBEAT_TTL_SECONDS - 60
        )
        isolated_registry.write_text(json.dumps(data))

        live = get_live_sessions()
        assert "sid-fresh" in live
        assert "sid-stale" not in live

    def test_include_headless_true_returns_headless_sessions(self, isolated_registry):
        register_session("sid-interactive", is_headless=False)
        register_session("sid-headless", is_headless=True)
        assert get_live_sessions(include_headless=True) == {
            "sid-interactive",
            "sid-headless",
        }

    def test_include_headless_false_excludes_headless_sessions(self, isolated_registry):
        """Headless sessions have no human watching live — their pendings
        must surface to interactive sessions, so they are excluded from
        the live-set used by the [ACTIONABLE] filter.
        """
        register_session("sid-interactive", is_headless=False)
        register_session("sid-headless", is_headless=True)
        assert get_live_sessions(include_headless=False) == {"sid-interactive"}

    def test_reflects_unregistration(self, isolated_registry):
        register_session("sid-a")
        register_session("sid-b")
        unregister_session("sid-a")
        assert get_live_sessions() == {"sid-b"}


# ---------------------------------------------------------------------------
# Legacy schema — entries without last_heartbeat are dead
# ---------------------------------------------------------------------------

class TestLegacySchema:
    """Old entries (pid + pid_create_time, no last_heartbeat) must be ignored.

    A registry left behind by the previous PID-based code carries no
    heartbeat field. get_live_sessions() should treat such entries as
    dead — they cannot have proved liveness under the new model.
    """

    def test_legacy_entry_without_heartbeat_is_dead(self, isolated_registry):
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(
            json.dumps(
                {
                    "sessions": {
                        "sid-legacy": {
                            "pid": 9999,
                            "pid_create_time": 12345.0,
                            "started_at": "2026-04-01T00:00:00+00:00",
                        }
                    }
                }
            )
        )
        assert "sid-legacy" not in get_live_sessions()

    def test_legacy_entry_does_not_break_iteration(self, isolated_registry):
        """A mixed registry (legacy + new) must not raise on iteration."""
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(
            json.dumps(
                {
                    "sessions": {
                        "sid-legacy": {
                            "pid": 9999,
                            "started_at": "2026-04-01T00:00:00+00:00",
                        },
                        "sid-modern": {
                            "started_at": "2026-05-01T00:00:00+00:00",
                            "is_headless": False,
                            "last_heartbeat": time.time(),
                        },
                    }
                }
            )
        )
        live = get_live_sessions()
        assert "sid-modern" in live
        assert "sid-legacy" not in live


# ---------------------------------------------------------------------------
# cleanup_stale_entries
# ---------------------------------------------------------------------------

class TestCleanupStaleEntries:
    """cleanup_stale_entries removes old/junk entries from the registry."""

    def test_removes_entries_past_grace_window(self, isolated_registry):
        register_session("sid-fresh")
        register_session("sid-old")

        data = json.loads(isolated_registry.read_text())
        data["sessions"]["sid-old"]["last_heartbeat"] = time.time() - 86400 - 60
        isolated_registry.write_text(json.dumps(data))

        removed = cleanup_stale_entries(grace_seconds=86400)
        assert removed == 1

        data = json.loads(isolated_registry.read_text())
        assert "sid-fresh" in data["sessions"]
        assert "sid-old" not in data["sessions"]

    def test_keeps_entries_within_grace_window(self, isolated_registry):
        register_session("sid-1")
        register_session("sid-2")
        register_session("sid-3")

        removed = cleanup_stale_entries(grace_seconds=86400)
        assert removed == 0

        data = json.loads(isolated_registry.read_text())
        assert set(data["sessions"].keys()) == {"sid-1", "sid-2", "sid-3"}

    def test_sweeps_legacy_entries_without_heartbeat(self, isolated_registry):
        """Entries with no last_heartbeat (legacy PID schema) are removed."""
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(
            json.dumps(
                {
                    "sessions": {
                        "sid-legacy": {"pid": 9999, "started_at": "old"},
                    }
                }
            )
        )
        removed = cleanup_stale_entries()
        assert removed == 1

        data = json.loads(isolated_registry.read_text())
        assert data["sessions"] == {}

    def test_returns_zero_on_empty_registry(self, isolated_registry):
        assert cleanup_stale_entries() == 0

    def test_respects_custom_grace_window(self, isolated_registry):
        register_session("sid-recent")
        data = json.loads(isolated_registry.read_text())
        data["sessions"]["sid-recent"]["last_heartbeat"] = time.time() - 120
        isolated_registry.write_text(json.dumps(data))

        # Grace of 60s: the entry (120s old) should be removed.
        removed = cleanup_stale_entries(grace_seconds=60)
        assert removed == 1


# ---------------------------------------------------------------------------
# Robustness: corrupt file handling
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_corrupt_json_resets_to_empty(self, isolated_registry):
        isolated_registry.write_text("this is not json {{{")
        assert get_live_sessions() == set()
        assert is_session_alive("anything") is False

    def test_corrupt_json_recovers_after_register(self, isolated_registry):
        isolated_registry.write_text("{not valid json")
        register_session("sid-recovery")
        data = json.loads(isolated_registry.read_text())
        assert "sid-recovery" in data["sessions"]

    def test_missing_sessions_key_resets(self, isolated_registry):
        isolated_registry.write_text(json.dumps({"other_key": "value"}))
        assert get_live_sessions() == set()

    def test_sessions_not_dict_resets(self, isolated_registry):
        isolated_registry.write_text(json.dumps({"sessions": ["not", "a", "dict"]}))
        assert get_live_sessions() == set()


# ---------------------------------------------------------------------------
# Concurrency: multiple threads registering different sessions
# ---------------------------------------------------------------------------

class TestConcurrency:
    """Atomic writes must keep the file valid under concurrent registration."""

    def test_sequential_registrations_preserve_all(self, isolated_registry):
        for i in range(20):
            register_session(f"sid-{i}")
        live = get_live_sessions()
        assert len(live) == 20
        for i in range(20):
            assert f"sid-{i}" in live

    def test_threaded_registrations_produce_valid_file(self, isolated_registry):
        register_session("seed")

        errors = []

        def worker(i):
            try:
                register_session(f"sid-{i}")
            except Exception as exc:  # noqa: BLE001 — test harness
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        text = isolated_registry.read_text()
        data = json.loads(text)
        assert "sessions" in data

    def test_atomic_write_no_tmp_leftover(self, isolated_registry):
        register_session("sid-atomic")
        parent = isolated_registry.parent
        stem = isolated_registry.stem
        leftovers = list(parent.glob(f"{stem}.tmp*"))
        assert leftovers == []


# ---------------------------------------------------------------------------
# Error surface
# ---------------------------------------------------------------------------

class TestErrors:
    def test_register_raises_on_save_failure(self, tmp_path, monkeypatch):
        fake_path = tmp_path / "registry.json"
        monkeypatch.setattr(
            session_registry, "_get_registry_path", lambda: fake_path
        )

        def _boom(data):
            raise SessionRegistryError("simulated I/O failure")

        monkeypatch.setattr(session_registry, "_save_registry", _boom)
        with pytest.raises(SessionRegistryError):
            register_session("sid")
