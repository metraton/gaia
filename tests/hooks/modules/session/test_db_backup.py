#!/usr/bin/env python3
"""The SessionStart backup of gaia.db: safe copy, verified, atomic, bounded, never blocking.

Every case builds a real SQLite database under GAIA_DATA_DIR in tmp_path, so the
Online Backup API and integrity_check run for real and ~/.gaia is never touched.
"""

import errno
import gzip
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from gaia.paths import backup  # noqa: E402
from modules.session import db_backup  # noqa: E402
from modules.session.db_backup import THROTTLE_SECONDS, maybe_backup_db, run_backup  # noqa: E402


def _make_db(path: Path, rows: int = 200, wal: bool = False) -> Path:
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany("INSERT INTO t (body) VALUES (?)", [("x" * 200,)] * rows)
    con.commit()
    con.close()
    return path


def _rows(snapshot: Path, tmp_path: Path) -> list[int]:
    plain = tmp_path / f"restored-{snapshot.name}.db"
    backup.restore_gzip(snapshot, plain)
    with sqlite3.connect(plain) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return [r[0] for r in con.execute("SELECT id FROM t ORDER BY id")]


def _snapshots(snap_dir: Path) -> list[Path]:
    return sorted(snap_dir.glob("sessionstart-*.db.gz"))


def _stray(snap_dir: Path) -> list[Path]:
    """Partial files a failed run must not leave behind."""
    return [p for p in snap_dir.iterdir() if p.name.startswith(".")] if snap_dir.exists() else []


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    db = _make_db(tmp_path / "gaia.db")
    return tmp_path, db, tmp_path / "snapshots"


def _status(data: Path) -> dict:
    return backup.read_status(data / "state" / backup.STATUS_FILE)


def test_a_snapshot_named_with_an_impossible_date_does_not_break_the_run(sandbox):
    data, _db, snap_dir = sandbox
    snap_dir.mkdir()
    odd = snap_dir / "sessionstart-20261399T000000000000.db.gz"
    odd.write_bytes(b"x")

    result = run_backup(force=True)

    assert result["result"] == "created"
    assert _status(data)["result"] == "created"
    assert odd.exists()


class TestSafeCopy:
    def test_first_run_writes_a_verified_restorable_gzip(self, sandbox, tmp_path):
        data, db, snap_dir = sandbox
        result = run_backup()
        assert result["result"] == "created"
        (snap,) = _snapshots(snap_dir)
        assert _rows(snap, tmp_path) == list(range(1, 201))
        status = _status(data)
        assert status["verification"] == "ok" and status["path"] == str(snap)
        assert status["size"] == snap.stat().st_size
        assert db.exists()

    def test_db_written_during_the_backup_yields_one_consistent_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("GAIA_DB", raising=False)
        db = _make_db(tmp_path / "gaia.db", rows=20000, wal=True)
        stop = threading.Event()
        errors: list[Exception] = []

        def writer():
            con = sqlite3.connect(db, timeout=30)
            try:
                while not stop.is_set():
                    con.execute("INSERT INTO t (body) VALUES ('w')")
                    con.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                con.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            time.sleep(0.05)
            result = run_backup()
        finally:
            stop.set()
            thread.join()
        assert result["result"] == "created", result
        assert not errors, "the backup must not make the session's writer fail"
        ids = _rows(_snapshots(tmp_path / "snapshots")[0], tmp_path)
        assert ids == list(range(1, len(ids) + 1)), "the copy is one committed state, no torn rows"
        assert len(ids) >= 20000


class TestFailuresKeepPriorBackups:
    def _prior(self, snap_dir: Path) -> tuple[Path, bytes]:
        result = run_backup()
        assert result["result"] == "created"
        (snap,) = _snapshots(snap_dir)
        return snap, snap.read_bytes()

    def test_corrupt_copy_is_not_kept(self, sandbox, monkeypatch):
        data, _, snap_dir = sandbox
        prior, content = self._prior(snap_dir)
        real_copy = backup._copy

        def corrupting_copy(source, dest_path):
            real_copy(source, dest_path)
            size = Path(dest_path).stat().st_size
            with open(dest_path, "r+b") as fh:
                fh.seek(4096)
                fh.write(os.urandom(size - 4096))

        monkeypatch.setattr(backup, "_copy", corrupting_copy)
        result = run_backup(force=True)

        assert result["result"] == "failed"
        assert _snapshots(snap_dir) == [prior] and prior.read_bytes() == content
        assert not _stray(snap_dir)
        status = _status(data)
        assert status["verification"].startswith("failed"), status

    def test_disk_full_while_compressing(self, sandbox, monkeypatch):
        data, _, snap_dir = sandbox
        prior, content = self._prior(snap_dir)

        def full(*_a, **_k):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(backup, "_compress", full)
        result = run_backup(force=True)

        assert result["result"] == "failed" and "No space left" in result["error"]
        assert _snapshots(snap_dir) == [prior] and prior.read_bytes() == content
        assert not _stray(snap_dir)
        assert "No space left" in _status(data)["error"]

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
    def test_unwritable_snapshots_dir_is_recorded_not_raised(self, sandbox):
        data, _, snap_dir = sandbox
        snap_dir.mkdir()
        snap_dir.chmod(0o500)
        try:
            result = run_backup()
        finally:
            snap_dir.chmod(0o700)
        assert result["result"] == "failed"
        assert _status(data)["result"] == "failed"
        assert not _snapshots(snap_dir)

    def test_unexpected_error_is_recorded(self, sandbox, monkeypatch):
        data, _, _ = sandbox

        def boom(*_a, **_k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(backup, "write_verified_gzip", boom)
        assert run_backup(force=True)["result"] == "failed"
        assert "disk on fire" in _status(data)["error"]


class TestThrottleAndConcurrency:
    def test_second_run_within_a_day_is_throttled(self, sandbox):
        _, _, snap_dir = sandbox
        assert run_backup()["result"] == "created"
        assert run_backup()["result"] == "throttled"
        assert len(_snapshots(snap_dir)) == 1

    def test_backup_taken_again_after_window(self, sandbox):
        _, _, snap_dir = sandbox
        run_backup()
        yesterday = datetime.now() - timedelta(seconds=THROTTLE_SECONDS + 3600)
        first = _snapshots(snap_dir)[0].rename(
            snap_dir / f"sessionstart-{yesterday.strftime('%Y%m%dT%H%M%S%f')}.db.gz"
        )
        os.utime(first, (yesterday.timestamp(), yesterday.timestamp()))
        assert run_backup()["result"] == "created"
        assert len(_snapshots(snap_dir)) == 2 and first.exists()

    def test_future_dated_snapshot_does_not_hold_the_throttle_forever(self, sandbox):
        """Clock skew: a snapshot stamped 30 days ahead neither blocks nor loops."""
        _, _, snap_dir = sandbox
        snap_dir.mkdir()
        ahead = datetime.now() + timedelta(days=30)
        future = snap_dir / f"sessionstart-{ahead.strftime('%Y%m%dT%H%M%S%f')}.db.gz"
        future.write_bytes(gzip.compress(b"x"))
        os.utime(future, (ahead.timestamp(), ahead.timestamp()))

        assert run_backup()["result"] == "created"
        assert run_backup()["result"] == "throttled"
        assert future.exists(), "a skewed clock must not destroy a backup"
        assert len(_snapshots(snap_dir)) == 2

    def test_two_sessions_at_once_take_one_backup(self, sandbox, monkeypatch):
        _, _, snap_dir = sandbox
        real_copy = backup._copy

        def slow_copy(source, dest_path):
            time.sleep(0.5)
            real_copy(source, dest_path)

        monkeypatch.setattr(backup, "_copy", slow_copy)
        barrier = threading.Barrier(2)
        results: list[dict] = []

        def session():
            barrier.wait()
            results.append(run_backup())

        threads = [threading.Thread(target=session) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(r["result"] for r in results) in (["created", "locked"], ["created", "throttled"])
        assert len(_snapshots(snap_dir)) == 1
        assert not (snap_dir / db_backup.LOCK_NAME).exists()

    def test_stale_lock_from_a_dead_run_is_reclaimed(self, sandbox):
        _, _, snap_dir = sandbox
        snap_dir.mkdir()
        lock = snap_dir / db_backup.LOCK_NAME
        lock.write_text("dead")
        old = time.time() - (db_backup.LOCK_STALE_SECONDS + 60)
        os.utime(lock, (old, old))
        assert run_backup()["result"] == "created"


class TestRetentionInTheJob:
    def test_uninstall_snapshots_are_untouched(self, sandbox):
        _, _, snap_dir = sandbox
        snap_dir.mkdir()
        uninstall = [snap_dir / f"uninstall-2025010{i}T000000000000.db.gz" for i in range(1, 9)]
        for snap in uninstall:
            snap.write_bytes(b"u")
        assert run_backup()["result"] == "created"
        assert all(s.exists() for s in uninstall)

    def test_old_dailies_are_pruned_by_gfs(self, sandbox):
        _, _, snap_dir = sandbox
        snap_dir.mkdir()
        start = datetime(2026, 1, 1, 9)
        for day in range(40):
            stamp = (start + timedelta(days=day)).strftime("%Y%m%dT%H%M%S%f")
            (snap_dir / f"sessionstart-{stamp}.db.gz").write_bytes(b"s")
        os.utime(max(_snapshots(snap_dir)), None)
        run_backup(force=True)
        assert len(_snapshots(snap_dir)) < 41
        assert len(_snapshots(snap_dir)) == len(backup.gfs_keep({
            p: backup.snapshot_stamp(p) for p in _snapshots(snap_dir)
        }))


class TestSessionStartNeverBlocks:
    def test_due_backup_runs_detached(self, sandbox, monkeypatch):
        calls = []

        class FakePopen:
            pid = 4242

            def __init__(self, argv, **kwargs):
                calls.append((argv, kwargs))

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        assert maybe_backup_db() == 4242
        argv, kwargs = calls[0]
        assert argv[-1] == "--run"
        assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs.get("start_new_session") or kwargs.get("creationflags")

    def test_throttled_start_spawns_nothing_and_never_opens_the_db(self, sandbox, monkeypatch):
        run_backup()
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened the DB"))
        assert maybe_backup_db() is None

    def test_missing_db_spawns_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("GAIA_DB", raising=False)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
        assert maybe_backup_db() is None

    def test_spawn_failure_never_raises(self, sandbox, monkeypatch):
        def broken(*_a, **_k):
            raise OSError("fork failed")

        monkeypatch.setattr(subprocess, "Popen", broken)
        assert maybe_backup_db() is None

    def test_detached_child_writes_the_backup(self, sandbox):
        data, _, snap_dir = sandbox
        assert maybe_backup_db() is not None
        deadline = time.time() + 30
        while time.time() < deadline and not (data / "state" / backup.STATUS_FILE).exists():
            time.sleep(0.1)
        assert _status(data)["result"] == "created"
        assert len(_snapshots(snap_dir)) == 1
