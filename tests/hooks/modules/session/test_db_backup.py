#!/usr/bin/env python3
"""
Tests for the SessionStart DB auto-backup (AC-7): modules.session.db_backup.

maybe_backup_db() throttles to at most one snapshot per 24h and retains the
newest 5 snapshots, non-fatally. It shares the create-snapshot-and-rotate
implementation with `gaia uninstall` via gaia.paths.snapshot.

Isolation: GAIA_DATA_DIR is redirected to a tmp path so db_path() and
snapshot_dir() resolve inside the test sandbox and the real ~/.gaia is never
touched.
"""

import sys
import time
from pathlib import Path

import pytest

# Add hooks to path so `from modules.session...` resolves correctly.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.session.db_backup import maybe_backup_db, THROTTLE_SECONDS, RETAIN


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect GAIA_DATA_DIR to tmp and create a fake gaia.db.

    Yields (data_dir, db_path, snapshot_dir).
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    db = tmp_path / "gaia.db"
    db.write_bytes(b"SQLite format 3\x00fake")
    snap_dir = tmp_path / "snapshots"
    yield tmp_path, db, snap_dir


class TestMaybeBackupThrottle:
    def test_first_launch_creates_snapshot(self, sandbox):
        _, db, snap_dir = sandbox
        path = maybe_backup_db()
        assert path is not None, "first SessionStart of the day must snapshot"
        assert Path(path).exists()
        assert db.exists(), "source DB must never be deleted"
        assert len(list(snap_dir.glob("*.db.gz"))) == 1

    def test_second_launch_same_day_does_not_snapshot(self, sandbox):
        """AC-7 throttle: a 2nd SessionStart within 24h creates NO new snapshot."""
        _, _, snap_dir = sandbox
        first = maybe_backup_db()
        assert first is not None
        second = maybe_backup_db()
        assert second is None, "2nd launch same day must be throttled"
        # Still exactly one snapshot on disk.
        assert len(list(snap_dir.glob("*.db.gz"))) == 1

    def test_backup_taken_again_after_window_elapses(self, sandbox):
        """When the newest snapshot is older than 24h, a new one is taken."""
        import os
        _, _, snap_dir = sandbox
        first = maybe_backup_db()
        assert first is not None
        # Backdate the only snapshot beyond the throttle window.
        old = time.time() - (THROTTLE_SECONDS + 3600)
        os.utime(first, (old, old))
        second = maybe_backup_db()
        assert second is not None, "a snapshot older than 24h must trigger a new backup"
        assert second != first
        assert len(list(snap_dir.glob("*.db.gz"))) == 2

    def test_force_bypasses_throttle(self, sandbox):
        _, _, snap_dir = sandbox
        assert maybe_backup_db() is not None
        assert maybe_backup_db(force=True) is not None
        assert len(list(snap_dir.glob("*.db.gz"))) == 2


class TestMaybeBackupRetention:
    def test_retention_keeps_exactly_five(self, sandbox):
        """Forcing 8 backups leaves exactly RETAIN (5) snapshots."""
        _, db, snap_dir = sandbox
        for _ in range(8):
            maybe_backup_db(force=True)
        snaps = sorted(snap_dir.glob("*.db.gz"))
        assert len(snaps) == RETAIN == 5
        assert db.exists(), "source DB must survive rotation"


class TestMaybeBackupNonFatal:
    def test_missing_db_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
        # No gaia.db created.
        assert maybe_backup_db() is None

    def test_never_raises_on_internal_error(self, sandbox, monkeypatch):
        """A failure inside create_snapshot must be swallowed (return None)."""
        import gaia.paths as paths
        def _boom(*a, **k):
            raise RuntimeError("disk on fire")
        monkeypatch.setattr(paths, "create_snapshot", _boom)
        # Must not raise.
        assert maybe_backup_db(force=True) is None


if __name__ == "__main__":
    import unittest
    unittest.main()
