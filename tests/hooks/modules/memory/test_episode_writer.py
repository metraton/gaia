#!/usr/bin/env python3
"""
Tests for episode_writer.write() failure visibility.

A rejected episodes INSERT used to end at an ERROR log line nobody tails:
writer.insert_episode converts an IntegrityError into an error dict,
episodic.store_episode re-raises it as RuntimeError, and episode_writer.write
catches that RuntimeError and returns None. The turn's contract still closes
COMPLETE and its agent.complete event still fires with the correct state --
the only trace of the lost episode was that log line. This pins the fix: a
persistence failure now also lands one row in ``harness_events``, queryable
via ``gaia query --surface harness_events`` long after the log has rotated.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.memory import episode_writer  # noqa: E402
from gaia.store import writer as store_writer  # noqa: E402


def _read_harness_rows(db_path):
    con = store_writer._connect(db_path)
    try:
        rows = con.execute(
            "SELECT type, agent, result, severity, payload "
            "FROM harness_events ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GAIA_WORKSPACE", "test_ws")
    return data_dir / "gaia.db"


class TestPersistFailureIsRecorded:
    def test_rejected_insert_leaves_a_harness_events_row(
        self, isolated_db, monkeypatch
    ):
        monkeypatch.setattr(
            store_writer,
            "insert_episode",
            lambda *a, **k: {
                "status": "error",
                "reason": "simulated_future_failure",
            },
        )

        result = episode_writer.write(
            {
                "agent": "gaia-system",
                "task_id": "task-abc",
                "session_id": "sess-abc",
                "plan_status": "COMPLETE",
                "prompt": "some turn",
            }
        )

        assert result is None, "write() still returns None on a rejected insert"

        rows = _read_harness_rows(isolated_db)
        failures = [r for r in rows if r["type"] == "episode.persist_failed"]
        assert failures, (
            "a rejected episode INSERT must leave a consultable trace in "
            "harness_events -- it left none before this fix"
        )
        assert failures[0]["severity"] == "error"
        assert failures[0]["agent"] == "gaia-system"
        assert "simulated_future_failure" in failures[0]["result"]

    def test_healthy_insert_leaves_no_failure_row(self, isolated_db):
        """The success path must stay silent -- no spurious failure event."""
        episode_writer.write(
            {
                "agent": "gaia-system",
                "task_id": "task-ok",
                "session_id": "sess-ok",
                "plan_status": "COMPLETE",
                "prompt": "a clean turn",
            }
        )
        rows = _read_harness_rows(isolated_db)
        failures = [r for r in rows if r["type"] == "episode.persist_failed"]
        assert not failures
