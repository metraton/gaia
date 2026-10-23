"""gaia.paths.backup: verified compressed copies, GFS retention, keep-newest-N, health."""

import gzip
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gaia.paths import backup


def _db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(1, 51)])
    con.commit()
    return con


def _snap(day: datetime) -> Path:
    return Path(f"sessionstart-{day.strftime('%Y%m%dT%H%M%S%f')}.db.gz")


class TestGfsTable:
    def test_keeps_seven_dailies_four_weeks_three_months_across_months(self):
        days = [datetime(2026, 7, 1, 10) + timedelta(days=n) for n in range(115)]
        assert days[-1].date() == datetime(2026, 10, 23).date()
        stamps = {_snap(d): d for d in days}
        early_same_day = datetime(2026, 10, 23, 1)
        stamps[_snap(early_same_day)] = early_same_day

        kept = sorted(d.strftime("%Y-%m-%d %H") for p, d in stamps.items() if p in backup.gfs_keep(stamps))

        assert kept == [
            "2026-08-31 10",
            "2026-09-30 10",
            "2026-10-04 10",
            "2026-10-11 10",
            "2026-10-17 10", "2026-10-18 10", "2026-10-19 10", "2026-10-20 10",
            "2026-10-21 10", "2026-10-22 10", "2026-10-23 10",
        ]

    def test_gaps_count_buckets_that_exist_not_calendar_days(self):
        days = [datetime(2026, 1, 5), datetime(2026, 3, 9), datetime(2026, 6, 1)]
        stamps = {_snap(d): d for d in days}
        assert backup.gfs_keep(stamps) == set(stamps)

    def test_iso_week_spans_the_year_boundary(self):
        dec31 = datetime(2026, 12, 31, 8)
        jan2 = datetime(2027, 1, 2, 8)
        stamps = {_snap(dec31): dec31, _snap(jan2): jan2}
        assert dec31.isocalendar()[:2] == jan2.isocalendar()[:2]
        assert backup.gfs_keep(stamps, daily=0, monthly=0) == {_snap(jan2)}


class TestImpossibleDate:
    def test_a_name_with_an_impossible_date_has_no_stamp(self):
        assert backup.snapshot_stamp(Path("sessionstart-20261399T000000000000.db.gz")) is None

    def test_pruning_and_keep_newest_skip_it_and_delete_nothing_else(self, tmp_path):
        odd = tmp_path / "sessionstart-20261399T000000000000.db.gz"
        good = tmp_path / "sessionstart-20260105T080000000000.db.gz"
        for path in (odd, good):
            path.write_bytes(b"x")

        assert backup.prune_gfs(tmp_path) == []
        assert backup.prune_newest(tmp_path, "sessionstart-*.db.gz", keep=1) == []
        assert odd.exists() and good.exists()


class TestVerifiedGzip:
    def test_round_trip(self, tmp_path):
        con = _db(tmp_path / "src.db")
        result = backup.write_verified_gzip(con, tmp_path / "out" / "x.db.gz")
        restored = tmp_path / "back.db"
        backup.restore_gzip(Path(result["path"]), restored)
        with sqlite3.connect(restored) as back:
            assert back.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 50
        assert result["verification"] == "ok"
        assert set(result["seconds"]) == {"copy", "verify", "compress"}

    def test_rejected_copy_leaves_nothing(self, tmp_path):
        con = _db(tmp_path / "src.db")
        target = tmp_path / "out" / "x.db.gz"
        with pytest.raises(backup.BackupVerificationError):
            backup.write_verified_gzip(con, target, accept=lambda dest: "rows differ")
        assert list(target.parent.iterdir()) == []


class TestKeepNewest:
    def test_keeps_newest_three_compressed_by_stamp_and_never_touches_plain_files(self, tmp_path):
        names = [f"gaia-v{v}-pre-migrate-2026010{d}T000000000000Z.db.gz" for v, d in
                 [(9, 1), (10, 2), (9, 3), (12, 4), (11, 5)]]
        for i, name in enumerate(names):
            (tmp_path / name).write_bytes(b"z")
            os.utime(tmp_path / name, (time.time() - i, time.time() - i))
        legacy = tmp_path / "gaia-v8-pre-migrate-20250101T000000000000Z.db"
        legacy.write_bytes(b"legacy")

        pruned = backup.prune_newest(tmp_path, "*-pre-migrate-*.db.gz", keep=3)

        assert sorted(Path(p).name for p in pruned) == sorted(names[:2])
        assert sorted(p.name for p in tmp_path.glob("*.db.gz")) == sorted(names[2:])
        assert legacy.read_bytes() == b"legacy"


class TestHealth:
    def _status(self, tmp_path, **fields):
        status = {"result": "created", "verification": "ok", "at": time.time()}
        status.update(fields)
        backup.write_status(tmp_path / "status.json", status)
        return tmp_path / "status.json"

    def _snapshot(self, snap_dir: Path, age_seconds: float) -> Path:
        snap_dir.mkdir(exist_ok=True)
        when = datetime.now() - timedelta(seconds=age_seconds)
        path = snap_dir / _snap(when)
        path.write_bytes(gzip.compress(b"x" * 1000))
        os.utime(path, (when.timestamp(), when.timestamp()))
        return path

    def test_fresh_verified_backup_passes(self, tmp_path):
        self._snapshot(tmp_path / "snapshots", 3600)
        health = backup.backup_health(tmp_path / "snapshots", self._status(tmp_path))
        assert health["severity"] == "pass"
        assert health["verification"] == "ok" and health["size"] > 0
        assert 3500 < health["age_seconds"] < 3700

    def test_older_than_48h_warns(self, tmp_path):
        self._snapshot(tmp_path / "snapshots", 49 * 3600)
        health = backup.backup_health(tmp_path / "snapshots", self._status(tmp_path))
        assert health["severity"] == "warning" and "48h" in health["detail"]

    def test_failed_last_attempt_warns_even_with_a_fresh_backup(self, tmp_path):
        self._snapshot(tmp_path / "snapshots", 60)
        status = self._status(tmp_path, result="failed", verification="failed: page 3 corrupt",
                              error="integrity_check: page 3 corrupt")
        health = backup.backup_health(tmp_path / "snapshots", status)
        assert health["severity"] == "warning" and "page 3 corrupt" in health["detail"]

    def test_no_backup_before_any_run_is_info_and_after_a_run_warns(self, tmp_path):
        health = backup.backup_health(tmp_path / "snapshots", tmp_path / "missing.json")
        assert health["severity"] == "info" and health["newest"] is None
        health = backup.backup_health(tmp_path / "snapshots", self._status(tmp_path))
        assert health["severity"] == "warning" and "no backup yet" in health["detail"]

    def test_uninstall_snapshot_is_not_the_newest_backup(self, tmp_path):
        snap_dir = tmp_path / "snapshots"
        self._snapshot(snap_dir, 72 * 3600)
        snap_dir.joinpath(f"uninstall-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.db.gz").write_bytes(b"u")
        health = backup.backup_health(snap_dir, self._status(tmp_path))
        assert health["severity"] == "warning"


def test_a_status_file_that_is_not_an_object_reads_as_no_status(tmp_path):
    status = tmp_path / "status.json"
    status.write_text("[1, 2]", encoding="utf-8")
    assert backup.read_status(status) == {}
