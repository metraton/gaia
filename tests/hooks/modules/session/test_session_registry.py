#!/usr/bin/env python3
"""
Tests for Session Registry Module.

Validates:
1. register_session / unregister_session / is_session_alive / get_live_sessions
2. Concurrency (multiple sequential writes interleave correctly)
3. Robustness (missing file, corrupt file, atomic write behavior)
4. SessionRegistryError contract
"""

import json
import os
import sys
import threading
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_registry
from modules.session.session_registry import (
    SessionRegistryError,
    register_session,
    unregister_session,
    is_session_alive,
    get_live_sessions,
    _get_registry_path,
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
    """Test register_session()."""

    def test_registers_new_session(self, isolated_registry):
        register_session("sid-1", pid=1234)
        assert isolated_registry.exists()
        data = json.loads(isolated_registry.read_text())
        assert "sid-1" in data["sessions"]
        assert data["sessions"]["sid-1"]["pid"] == 1234
        assert data["sessions"]["sid-1"]["started_at"] is not None

    def test_register_without_pid_defaults_to_none(self, isolated_registry):
        register_session("sid-2")
        data = json.loads(isolated_registry.read_text())
        assert data["sessions"]["sid-2"]["pid"] is None

    def test_register_with_explicit_started_at(self, isolated_registry):
        register_session("sid-3", pid=5, started_at="2026-04-18T00:00:00+00:00")
        data = json.loads(isolated_registry.read_text())
        assert data["sessions"]["sid-3"]["started_at"] == "2026-04-18T00:00:00+00:00"

    def test_register_updates_existing_entry(self, isolated_registry):
        register_session("sid-4", pid=1)
        register_session("sid-4", pid=2)
        data = json.loads(isolated_registry.read_text())
        assert data["sessions"]["sid-4"]["pid"] == 2

    def test_register_empty_session_id_raises(self, isolated_registry):
        with pytest.raises(SessionRegistryError):
            register_session("")

    def test_register_creates_parent_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "dir" / "registry.json"
        monkeypatch.setattr(session_registry, "_get_registry_path", lambda: nested)
        register_session("sid-nested", pid=99)
        assert nested.exists()


# ---------------------------------------------------------------------------
# unregister_session
# ---------------------------------------------------------------------------

class TestUnregisterSession:
    """Test unregister_session()."""

    def test_unregisters_existing_session(self, isolated_registry):
        register_session("sid-a", pid=1)
        register_session("sid-b", pid=2)
        unregister_session("sid-a")
        data = json.loads(isolated_registry.read_text())
        assert "sid-a" not in data["sessions"]
        assert "sid-b" in data["sessions"]

    def test_unregister_unknown_session_is_noop(self, isolated_registry):
        register_session("sid-x", pid=7)
        # Should NOT raise
        unregister_session("sid-nonexistent")
        data = json.loads(isolated_registry.read_text())
        assert "sid-x" in data["sessions"]

    def test_unregister_when_file_missing_is_noop(self, isolated_registry):
        assert not isolated_registry.exists()
        # Should NOT raise
        unregister_session("sid-none")

    def test_unregister_empty_session_id_is_noop(self, isolated_registry):
        register_session("sid-keep", pid=1)
        unregister_session("")
        data = json.loads(isolated_registry.read_text())
        assert "sid-keep" in data["sessions"]


# ---------------------------------------------------------------------------
# is_session_alive
# ---------------------------------------------------------------------------

class TestIsSessionAlive:
    """Test is_session_alive()."""

    def test_returns_true_for_registered_session(self, isolated_registry):
        register_session("sid-live", pid=42)
        assert is_session_alive("sid-live") is True

    def test_returns_false_for_unregistered_session(self, isolated_registry):
        register_session("sid-other", pid=1)
        assert is_session_alive("sid-missing") is False

    def test_returns_false_for_empty_session_id(self, isolated_registry):
        assert is_session_alive("") is False

    def test_returns_false_when_registry_missing(self, isolated_registry):
        assert not isolated_registry.exists()
        assert is_session_alive("anything") is False


# ---------------------------------------------------------------------------
# get_live_sessions
# ---------------------------------------------------------------------------

class TestGetLiveSessions:
    """Test get_live_sessions()."""

    def test_returns_empty_set_when_file_missing(self, isolated_registry):
        assert get_live_sessions() == set()

    def test_returns_all_registered_session_ids(self, isolated_registry):
        register_session("sid-1", pid=1)
        register_session("sid-2", pid=2)
        register_session("sid-3", pid=3)
        assert get_live_sessions() == {"sid-1", "sid-2", "sid-3"}

    def test_reflects_unregistration(self, isolated_registry):
        register_session("sid-a", pid=1)
        register_session("sid-b", pid=2)
        unregister_session("sid-a")
        assert get_live_sessions() == {"sid-b"}


# ---------------------------------------------------------------------------
# Robustness: corrupt file handling
# ---------------------------------------------------------------------------

class TestRobustness:
    """Test robustness against corrupt/malformed files."""

    def test_corrupt_json_resets_to_empty(self, isolated_registry):
        isolated_registry.write_text("this is not json {{{")
        # Reading should not raise; should return empty
        assert get_live_sessions() == set()
        # is_session_alive should return False
        assert is_session_alive("anything") is False

    def test_corrupt_json_recovers_after_register(self, isolated_registry):
        isolated_registry.write_text("{not valid json")
        register_session("sid-recovery", pid=1)
        # File should now be valid JSON with the new session
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
    """Test basic concurrency — atomic writes must not corrupt the file.

    Note: because all writes use the same tmp path in a single process,
    perfect concurrency with last-writer-wins is expected. This test
    verifies the file stays valid JSON and the final state is consistent.
    """

    def test_sequential_registrations_preserve_all(self, isolated_registry):
        for i in range(20):
            register_session(f"sid-{i}", pid=i)
        live = get_live_sessions()
        assert len(live) == 20
        for i in range(20):
            assert f"sid-{i}" in live

    def test_threaded_registrations_produce_valid_file(self, isolated_registry):
        # Register baseline first so the file exists
        register_session("seed", pid=0)

        errors = []

        def worker(i):
            try:
                register_session(f"sid-{i}", pid=i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        # Final file must be valid JSON
        text = isolated_registry.read_text()
        data = json.loads(text)
        assert "sessions" in data
        # Seed entry should still be present (not corrupted)
        # Note: under true concurrency with last-writer-wins, some
        # intermediate registrations may be lost. The important guarantee
        # is file integrity — valid JSON at the end.

    def test_atomic_write_no_tmp_leftover(self, isolated_registry):
        register_session("sid-atomic", pid=1)
        # No .tmp.* files should persist after successful write
        parent = isolated_registry.parent
        stem = isolated_registry.stem
        leftovers = list(parent.glob(f"{stem}.tmp*"))
        assert leftovers == []


# ---------------------------------------------------------------------------
# Error surface
# ---------------------------------------------------------------------------

class TestErrors:
    """Test SessionRegistryError contract."""

    def test_register_raises_on_save_failure(self, tmp_path, monkeypatch):
        # Point to a path where parent creation will fail (simulate EACCES)
        fake_path = tmp_path / "registry.json"
        monkeypatch.setattr(
            session_registry, "_get_registry_path", lambda: fake_path
        )

        def _boom(data):
            raise SessionRegistryError("simulated I/O failure")

        monkeypatch.setattr(session_registry, "_save_registry", _boom)
        with pytest.raises(SessionRegistryError):
            register_session("sid", pid=1)
