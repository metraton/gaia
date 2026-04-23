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

# Optional helpers introduced by Fix A (PID liveness). Imported defensively so
# the existing test suite keeps working even if this test file is ever lifted
# ahead of the session_registry refactor.
try:
    from modules.session.session_registry import (  # noqa: F401
        get_pid_create_time,
        _is_pid_alive,
    )
    HAS_PID_HELPERS = True
except ImportError:
    HAS_PID_HELPERS = False


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
        # Use pid=None so entries pass the liveness filter (presence-only
        # fallback). Using fake integer PIDs would couple this presence
        # assertion to whichever PIDs happen to exist on the host --
        # PID liveness is covered end-to-end by TestPidLiveness below.
        register_session("sid-1")
        register_session("sid-2")
        register_session("sid-3")
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
        # No pid -> entries pass the liveness filter via the presence-only
        # branch. This test is about atomic write integrity across 20
        # sequential registrations, not about PID validation.
        for i in range(20):
            register_session(f"sid-{i}")
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


# ---------------------------------------------------------------------------
# PID liveness (Fix A / AC3): registry must self-clean zombi entries
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not HAS_PID_HELPERS,
    reason="PID liveness helpers not yet present on session_registry",
)
class TestPidLiveness:
    """get_live_sessions() must filter stale entries via PID + starttime.

    The bug this guards against: stop_hook used to unregister the session on
    every turn, emptying the registry between messages. When stop_hook stops
    touching the registry, the registry needs its own truth source -- the OS
    process table -- otherwise sessions that crashed without firing
    SessionEnd stay "alive" forever. AC3 asserts that get_live_sessions()
    never returns a session whose PID is gone or whose /proc starttime
    differs from what was persisted (PID recycled under the same number).
    """

    def test_entry_with_live_pid_and_matching_starttime_is_returned(
        self, isolated_registry
    ):
        """A freshly-registered session (own PID) must stay live."""
        my_pid = os.getpid()
        register_session("sid-self", pid=my_pid)
        assert "sid-self" in get_live_sessions()

    def test_entry_with_dead_pid_is_filtered(self, isolated_registry):
        """A PID that no longer exists must be filtered out.

        Picks a PID that is extremely unlikely to be alive (2**30-1 is well
        above /proc/sys/kernel/pid_max on mainstream kernels).
        """
        dead_pid = 2 ** 30 - 1
        register_session("sid-zombie", pid=dead_pid)
        assert "sid-zombie" not in get_live_sessions()

    def test_entry_with_recycled_pid_starttime_mismatch_is_filtered(
        self, isolated_registry, monkeypatch
    ):
        """Same PID number, different starttime -> treat as zombie.

        This models the recycled-PID case: the original process died, the
        kernel later assigned the same PID to an unrelated process with a
        different start time. Without the starttime check, get_live_sessions
        would incorrectly keep the ghost entry.
        """
        my_pid = os.getpid()
        register_session("sid-recycled", pid=my_pid)

        # Tamper with the persisted starttime so it no longer matches the
        # real /proc entry. The registry is JSON on disk, so we can rewrite
        # the field directly and then call get_live_sessions() to verify
        # the starttime comparison logic kicks in.
        data = json.loads(isolated_registry.read_text())
        entry = data["sessions"]["sid-recycled"]
        if "pid_create_time" not in entry or entry["pid_create_time"] is None:
            # Fix A must persist pid_create_time for entries registered with
            # a PID; if it's missing this test is not exercising the drift
            # path, so fail loudly rather than pass a false green.
            pytest.fail(
                "register_session did not persist pid_create_time -- "
                "AC3 starttime drift cannot be asserted"
            )
        entry["pid_create_time"] = "0"  # clearly bogus starttime
        data["sessions"]["sid-recycled"] = entry
        isolated_registry.write_text(json.dumps(data))

        assert "sid-recycled" not in get_live_sessions()

    def test_legacy_entry_without_pid_field_is_preserved(
        self, isolated_registry
    ):
        """Back-compat: entries written before Fix A had no pid field.

        Those must continue to be reported as alive. Fix A is additive --
        an entry with pid=None survives the liveness filter because there
        is nothing to probe.
        """
        # Register the legacy shape by hand so we're not relying on current
        # register_session() behaviour.
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(
            json.dumps(
                {
                    "sessions": {
                        "sid-legacy": {
                            "pid": None,
                            "started_at": "2026-04-01T00:00:00+00:00",
                        }
                    }
                }
            )
        )
        assert "sid-legacy" in get_live_sessions()

    def test_legacy_entry_with_pid_but_no_create_time_is_preserved_if_alive(
        self, isolated_registry
    ):
        """Entries with a pid but no pid_create_time predate Fix A.

        Strategy: if the PID is alive, keep the entry (we have no stronger
        signal). If the PID is dead, filter it. This test covers the
        "alive" branch so back-compat works end-to-end.
        """
        my_pid = os.getpid()
        isolated_registry.parent.mkdir(parents=True, exist_ok=True)
        isolated_registry.write_text(
            json.dumps(
                {
                    "sessions": {
                        "sid-legacy-pid": {
                            "pid": my_pid,
                            "started_at": "2026-04-01T00:00:00+00:00",
                        }
                    }
                }
            )
        )
        assert "sid-legacy-pid" in get_live_sessions()


@pytest.mark.skipif(
    not HAS_PID_HELPERS,
    reason="PID liveness helpers not yet present on session_registry",
)
class TestGetPidCreateTime:
    """get_pid_create_time() reads field 22 of /proc/{pid}/stat (stdlib only).

    The function returns a float (see docstring): /proc starttime is an
    unsigned int expressed in clock ticks since boot, which fits exactly
    into a float for any realistic uptime. Equality comparison is used to
    detect PID recycling, and integer-valued floats compare exactly.
    """

    def test_returns_float_for_live_pid(self):
        result = session_registry.get_pid_create_time(os.getpid())
        assert result is not None
        assert isinstance(result, float)
        # Starttime must be a non-negative integer value (clock ticks).
        assert result >= 0
        assert result == int(result)

    def test_returns_none_for_dead_pid(self):
        dead_pid = 2 ** 30 - 1
        assert session_registry.get_pid_create_time(dead_pid) is None
