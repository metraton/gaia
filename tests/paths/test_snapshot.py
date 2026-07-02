"""
Tests for gaia.paths.snapshot -- the shared "create gzip snapshot + rotate"
helper used by BOTH `gaia uninstall` (AC-6) and the SessionStart auto-backup
(AC-7).

Invariants under test:
  * COPY-based: the source DB is never moved/deleted -- only read.
  * Snapshot content matches the source byte-for-byte (through gzip).
  * Retention keeps exactly the newest N snapshots and prunes the rest.
  * latest_snapshot_age_seconds reports the freshest snapshot's age.
"""

import gzip
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gaia.paths.snapshot import (
    DEFAULT_RETAIN,
    create_snapshot,
    enforce_retention,
    latest_snapshot_age_seconds,
)


class TestCreateSnapshot(unittest.TestCase):
    def test_creates_gzip_matching_source_and_preserves_db(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            payload = b"SQLite format 3\x00some-bytes"
            db.write_bytes(payload)
            snap_dir = Path(tmp) / "snapshots"

            res = create_snapshot(db, snap_dir, prefix="test")

            self.assertTrue(res["created"])
            self.assertTrue(db.exists(), "source DB must never be deleted")
            self.assertEqual(db.read_bytes(), payload, "source DB must be unmodified")
            snap = Path(res["path"])
            self.assertTrue(snap.exists())
            self.assertTrue(snap.name.endswith(".db.gz"))
            with gzip.open(snap, "rb") as f:
                self.assertEqual(f.read(), payload)

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            db.write_bytes(b"x")
            snap_dir = Path(tmp) / "snapshots"
            res = create_snapshot(db, snap_dir, dry_run=True, prefix="test")
            self.assertFalse(res["created"])
            self.assertFalse(snap_dir.exists())

    def test_missing_db_is_noop_not_error(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "ghost.db"
            snap_dir = Path(tmp) / "snapshots"
            res = create_snapshot(db, snap_dir, prefix="test")
            self.assertFalse(res["created"])
            self.assertIsNone(res["path"])
            self.assertNotIn("error", res)

    def test_create_snapshot_enforces_retention(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            db.write_bytes(b"data")
            snap_dir = Path(tmp) / "snapshots"

            # Create more than DEFAULT_RETAIN snapshots via the helper.
            for _ in range(DEFAULT_RETAIN + 4):
                create_snapshot(db, snap_dir, retain=DEFAULT_RETAIN, prefix="test")

            snaps = sorted(snap_dir.glob("*.db.gz"))
            self.assertEqual(len(snaps), DEFAULT_RETAIN)


class TestEnforceRetention(unittest.TestCase):
    def test_keeps_exactly_n_newest(self):
        with TemporaryDirectory() as tmp:
            snap_dir = Path(tmp) / "snapshots"
            snap_dir.mkdir()
            # Timestamp-sortable names, oldest first.
            names = [f"test-2026010{i}T000000000000.db.gz" for i in range(1, 9)]
            for n in names:
                (snap_dir / n).write_bytes(b"x")

            pruned = enforce_retention(snap_dir, retain=5)

            survivors = sorted(p.name for p in snap_dir.glob("*.db.gz"))
            self.assertEqual(len(survivors), 5)
            # The 5 newest (lexically largest) survive.
            self.assertEqual(survivors, sorted(names)[-5:])
            # The 3 oldest were pruned.
            self.assertEqual(len(pruned), 3)

    def test_no_prune_when_under_limit(self):
        with TemporaryDirectory() as tmp:
            snap_dir = Path(tmp) / "snapshots"
            snap_dir.mkdir()
            for i in range(3):
                (snap_dir / f"test-2026010{i}T000000000000.db.gz").write_bytes(b"x")
            pruned = enforce_retention(snap_dir, retain=5)
            self.assertEqual(pruned, [])
            self.assertEqual(len(list(snap_dir.glob("*.db.gz"))), 3)

    def test_missing_dir_is_noop(self):
        with TemporaryDirectory() as tmp:
            snap_dir = Path(tmp) / "does-not-exist"
            self.assertEqual(enforce_retention(snap_dir, retain=5), [])


class TestLatestSnapshotAge(unittest.TestCase):
    def test_none_when_no_snapshots(self):
        with TemporaryDirectory() as tmp:
            snap_dir = Path(tmp) / "snapshots"
            self.assertIsNone(latest_snapshot_age_seconds(snap_dir))
            snap_dir.mkdir()
            self.assertIsNone(latest_snapshot_age_seconds(snap_dir))

    def test_reports_recent_age_for_fresh_snapshot(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            db.write_bytes(b"data")
            snap_dir = Path(tmp) / "snapshots"
            create_snapshot(db, snap_dir, prefix="test")
            age = latest_snapshot_age_seconds(snap_dir)
            self.assertIsNotNone(age)
            self.assertLess(age, 60, "a just-created snapshot must read as seconds old")

    def test_reports_old_age_when_mtime_backdated(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "gaia.db"
            db.write_bytes(b"data")
            snap_dir = Path(tmp) / "snapshots"
            res = create_snapshot(db, snap_dir, prefix="test")
            snap = Path(res["path"])
            old = time.time() - (48 * 60 * 60)  # 48h ago
            import os
            os.utime(snap, (old, old))
            age = latest_snapshot_age_seconds(snap_dir)
            self.assertGreater(age, 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
