#!/usr/bin/env python3
"""End-to-end (AC-7): the real session_start.py hook auto-backs-up gaia.db,
throttled to once per 24h, and never mutates the source DB.

Drives the hook the way Claude Code does -- pipe a SessionStart event on
stdin -- with GAIA_DATA_DIR redirected to a tmp sandbox so db_path() and
snapshot_dir() resolve inside the test and the real ~/.gaia is untouched.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[2] / "hooks" / "session_start.py"


def _run_session_start(cwd: Path, env: dict, sid: str) -> subprocess.CompletedProcess:
    payload = json.dumps(
        {"hook_event_name": "SessionStart", "session_id": sid, "matcher": "startup"}
    )
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=30,
    )


def _wait_for_run(status_path: Path, after: float, timeout: float = 20.0) -> dict:
    """The backup runs detached, so wait for a status written after ``after``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {}
        if status.get("at", 0.0) > after:
            return status
        time.sleep(0.1)
    pytest.fail(f"no backup status recorded within {timeout}s at {status_path}")


@pytest.fixture
def sandbox(tmp_path):
    data_dir = tmp_path / "gaia-data"
    data_dir.mkdir()
    db = data_dir / "gaia.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE t (payload TEXT)")
        con.execute("INSERT INTO t VALUES ('payload')")
    con.close()

    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()

    env = os.environ.copy()
    env["GAIA_DATA_DIR"] = str(data_dir)
    env["HOME"] = str(tmp_path)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    return workspace, env, data_dir, db


def test_first_launch_snapshots_second_launch_throttled(sandbox):
    workspace, env, data_dir, db = sandbox
    db_hash_before = hashlib.sha256(db.read_bytes()).hexdigest()
    snap_dir = data_dir / "snapshots"
    status_path = data_dir / "state" / "db-backup-status.json"

    p1 = _run_session_start(workspace, env, "sess-1")
    assert p1.returncode == 0, f"launch 1 failed: {p1.stderr[-500:]!r}"
    first = _wait_for_run(status_path, after=0.0)
    assert first["result"] == "created", first
    snaps_after_1 = sorted(snap_dir.glob("*.db.gz")) if snap_dir.exists() else []
    assert len(snaps_after_1) == 1, "first SessionStart of the day must snapshot once"

    p2 = _run_session_start(workspace, env, "sess-2")
    assert p2.returncode == 0, f"launch 2 failed: {p2.stderr[-500:]!r}"
    snaps_after_2 = sorted(snap_dir.glob("*.db.gz"))
    assert snaps_after_2 == snaps_after_1, (
        "2nd SessionStart same day must be throttled -- no new snapshot"
    )

    # Source DB never mutated or deleted.
    assert db.exists()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == db_hash_before


if __name__ == "__main__":
    import unittest
    unittest.main()
