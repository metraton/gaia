"""Tests for automatic episode retention (gaia.store.writer.prune_episodes).

The ``episodes`` table had no DB-side retention; only the legacy filesystem
layout was pruned. ``prune_episodes`` deletes rows older than the retention
window (90 days by default). The schema's ``episodes_ad`` AFTER DELETE trigger
must keep ``episodes_fts`` synchronized -- these tests bootstrap a REAL DB (so
the FTS virtual table and its triggers exist) and verify both the row-level
deletion and the FTS consistency.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.store.writer import insert_episode, prune_episodes, EPISODE_RETENTION_DAYS


@pytest.fixture()
def bootstrapped_db(tmp_path, monkeypatch):
    """Bootstrap a real gaia.db (full schema incl. episodes_fts + triggers)."""
    bootstrap = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
    db_path = tmp_path / "gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    res = subprocess.run(
        ["bash", str(bootstrap)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode == 0, (
        f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    # Keep prune deterministic: never auto-fire the probabilistic gate during
    # these tests; we call prune_episodes explicitly. (Rate high => ~never.)
    monkeypatch.setenv("GAIA_EPISODE_PRUNE_SAMPLE_RATE", "100000")
    return db_path


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _insert(db_path, episode_id, days_ago):
    res = insert_episode(
        "me",
        episode_id,
        {
            "timestamp": _iso_days_ago(days_ago),
            "agent": "developer",
            "prompt": f"prompt for {episode_id}",
            "plan_status": "COMPLETE",
        },
        db_path=db_path,
    )
    assert res["status"] == "applied", res
    return episode_id


def _episode_ids(db_path) -> set:
    con = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in con.execute("SELECT episode_id FROM episodes")}
    finally:
        con.close()


def _fts_episode_ids(db_path) -> set:
    con = sqlite3.connect(str(db_path))
    try:
        return {r[0] for r in con.execute("SELECT episode_id FROM episodes_fts")}
    finally:
        con.close()


class TestPruneEpisodes:
    def test_deletes_only_rows_older_than_cutoff(self, bootstrapped_db):
        db = bootstrapped_db
        _insert(db, "recent-1", days_ago=1)
        _insert(db, "recent-2", days_ago=89)
        _insert(db, "old-1", days_ago=91)
        _insert(db, "old-2", days_ago=365)

        result = prune_episodes(cutoff_days=90, db_path=db)

        assert result["status"] == "applied"
        assert result["deleted"] == 2
        remaining = _episode_ids(db)
        assert remaining == {"recent-1", "recent-2"}

    def test_fts_stays_consistent_after_prune(self, bootstrapped_db):
        """The episodes_ad AFTER DELETE trigger must drop the FTS rows too."""
        db = bootstrapped_db
        _insert(db, "keep", days_ago=10)
        _insert(db, "drop", days_ago=200)

        # Precondition: both are indexed in FTS after insert.
        assert _fts_episode_ids(db) == {"keep", "drop"}

        prune_episodes(cutoff_days=90, db_path=db)

        # The pruned row is gone from BOTH the base table and the FTS index.
        assert _episode_ids(db) == {"keep"}
        assert _fts_episode_ids(db) == {"keep"}

    def test_fts_search_returns_no_pruned_rows(self, bootstrapped_db):
        """A full-text query must not surface a pruned episode's content."""
        db = bootstrapped_db
        _insert(db, "old-searchable", days_ago=200)
        prune_episodes(cutoff_days=90, db_path=db)

        con = sqlite3.connect(str(db))
        try:
            # Quote the term as an FTS5 phrase; a bare hyphen is the NOT
            # operator and "searchable" would be read as a column name.
            hits = con.execute(
                "SELECT episode_id FROM episodes_fts WHERE episodes_fts MATCH ?",
                ('"old-searchable"',),
            ).fetchall()
        finally:
            con.close()
        assert hits == []

    def test_default_retention_is_90_days(self):
        assert EPISODE_RETENTION_DAYS == 90

    def test_prune_on_empty_table_is_noop(self, bootstrapped_db):
        result = prune_episodes(cutoff_days=90, db_path=bootstrapped_db)
        assert result["status"] == "applied"
        assert result["deleted"] == 0

    def test_auto_prune_fires_when_rate_forces_it(self, bootstrapped_db, monkeypatch):
        """With sample rate == 1, every insert triggers a prune sweep, so an
        old row is removed on the next insert -- proving the automatic path."""
        db = bootstrapped_db
        _insert(db, "stale", days_ago=300)
        # Force the probabilistic gate to fire on every insert.
        monkeypatch.setenv("GAIA_EPISODE_PRUNE_SAMPLE_RATE", "1")
        _insert(db, "fresh", days_ago=1)
        assert _episode_ids(db) == {"fresh"}
