"""Migration v62 -> v63 turns every enabled schedule into a routine that is due.

`gaia schedule` is retired, but its four tables stay in the database, unused, so
no row is lost. The migration only inserts: each ENABLED scheduled task becomes
one open routine, due at once, pointing at the skill of the same name; a
disabled one produces nothing. Reports are untouched and due as before.

The database under test is the newest published base (v57) carrying schedule
rows and reports, upgraded through the real `gaia migrate`. Everything lives in
a tmp directory; the user's ~/.gaia is never read.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA = _REPO_ROOT / "bin" / "gaia"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v62_to_v63.sql"
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "published_bases"

sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
sys.path.insert(0, str(_FIXTURES))
import migration_guard  # noqa: E402
from loader import load_base  # noqa: E402

from gaia.store.reader import list_unread_notifications  # noqa: E402
from gaia.store.writer import ack_task_notification  # noqa: E402

PUBLISHED_BASE = 57
RETIRED_TABLES = ("scheduled_tasks", "scheduled_task_machines", "scheduled_task_state",
                  "schedule_suspensions")

FRIDAYS = {"kind": "calendar", "minute": 30, "hour": 7, "day_of_month": None,
           "month": None, "day_of_week": [5, 1, 3]}
EVERY_SIX_HOURS = {"kind": "interval", "every_seconds": 21600}


def _gaia_migrate(tmp: Path, db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GAIA_DB": str(db), "GAIA_DATA_DIR": str(tmp), "WORKSPACE": str(tmp)}
    return subprocess.run(
        [sys.executable, str(_GAIA), "migrate", *args, "--db", str(db)],
        env=env, capture_output=True, text=True, check=False,
    )


def _rows(db: Path, sql: str) -> list[dict]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(sql)]
    finally:
        con.close()


def _seed(db: Path, enabled_spec: dict) -> str:
    """Two schedules (one enabled, one disabled) with their dependants, and two reports."""
    con = sqlite3.connect(db)
    try:
        workspace = con.execute("SELECT name FROM workspaces LIMIT 1").fetchone()[0]
        enabled = con.execute(
            "INSERT INTO scheduled_tasks (workspace, name, schedule_spec, enabled) "
            "VALUES (?, 'gmail-triage', ?, 1)", (workspace, json.dumps(enabled_spec))
        ).lastrowid
        con.execute(
            "INSERT INTO scheduled_tasks (workspace, name, schedule_spec, enabled) "
            "VALUES (?, 'retired-probe', ?, 0)", (workspace, json.dumps(EVERY_SIX_HOURS))
        )
        con.execute("INSERT INTO scheduled_task_machines (task_id, machine_name) "
                    "VALUES (?, 'some-machine')", (enabled,))
        con.execute("INSERT INTO scheduled_task_state (task_id, machine_name, installed) "
                    "VALUES (?, 'some-machine', 1)", (enabled,))
        con.execute("INSERT INTO schedule_suspensions (workspace, task_id, until, reason) "
                    "VALUES (?, ?, '2026-01-01T00:00:00Z', 'paused')", (workspace, enabled))
        con.execute("INSERT INTO task_notifications (workspace, task_name, headline, unread) "
                    "VALUES (?, 'nightly', 'unread report', 1)", (workspace,))
        con.execute("INSERT INTO task_notifications (workspace, task_name, headline, unread) "
                    "VALUES (?, 'nightly', 'seen report', 0)", (workspace,))
        con.commit()
        return workspace
    finally:
        con.close()


def _upgrade(tmp_path: Path, enabled_spec: dict):
    """Seed the published base, upgrade it, and return what a test compares against."""
    db = tmp_path / "gaia.db"
    load_base(PUBLISHED_BASE, db)
    workspace = _seed(db, enabled_spec)
    retired_before = {table: _rows(db, f"SELECT * FROM {table} ORDER BY 1, 2")
                      for table in RETIRED_TABLES}

    applied = _gaia_migrate(tmp_path, db, "apply")
    if applied.returncode != 0:
        label = migration_guard.chain_label(PUBLISHED_BASE, _expected_version())
        assert f"--consent-chain {label}" in applied.stderr, applied.stderr
        applied = _gaia_migrate(tmp_path, db, "apply", "--consent-chain", label)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return db, workspace, retired_before


def _expected_version() -> int:
    text = (_REPO_ROOT / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    return int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.M).group(1))


def test_an_enabled_schedule_becomes_one_due_routine_and_nothing_else_changes(tmp_path):
    db, workspace, retired_before = _upgrade(tmp_path, FRIDAYS)

    routines = _rows(db, "SELECT * FROM task_notifications WHERE kind = 'routine'")
    assert [(r["task_name"], r["pointer_kind"], r["pointer_ref"]) for r in routines] == [
        ("gmail-triage", "skill", "gmail-triage")
    ]
    routine = routines[0]
    assert routine["workspace"] == workspace
    assert routine["due_at"] is None and routine["closed_at"] is None and routine["unread"] == 0
    assert routine["headline"] and "retired-probe" not in routine["headline"]

    due = list_unread_notifications(workspace=workspace, db_path=db)
    assert sorted((row["kind"], row["headline"]) for row in due) == [
        ("report", "unread report"), ("routine", routine["headline"]),
    ], "the unread report stays due as before and the converted routine is due at once"

    retired_after = {table: _rows(db, f"SELECT * FROM {table} ORDER BY 1, 2")
                     for table in RETIRED_TABLES}
    assert retired_after == retired_before, "the four retired tables keep every row"


def test_the_converted_routine_advances_when_it_is_done(tmp_path):
    for spec in (FRIDAYS, EVERY_SIX_HOURS):
        run_dir = tmp_path / ("calendar" if spec["kind"] == "calendar" else "interval")
        run_dir.mkdir()
        db, _, _ = _upgrade(run_dir, spec)
        routine_id = _rows(db, "SELECT id FROM task_notifications WHERE kind = 'routine'")[0]["id"]

        done = ack_task_notification(routine_id, db_path=db)

        assert done["action"] == "advanced", done
        assert datetime.fromisoformat(done["due_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)


def test_the_migration_is_backward_compatible_and_needs_no_consent(tmp_path):
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert migration_guard.compat_mark(sql) == "backward"

    db, _, _ = _upgrade(tmp_path, FRIDAYS)
    backup = next((tmp_path / "backups").glob("*.db"))
    con = sqlite3.connect(backup)
    try:
        census = migration_guard.take_census(con)
    finally:
        con.close()
    assert census["scheduled_tasks"] == 2, "the census must see the rows the gate protects"
    assert migration_guard.scan(sql, census) == ()


def test_running_the_migration_again_changes_nothing(tmp_path):
    db, _, _ = _upgrade(tmp_path, FRIDAYS)
    before = _rows(db, "SELECT * FROM task_notifications ORDER BY id")

    con = sqlite3.connect(db)
    try:
        con.executescript(_MIGRATION.read_text(encoding="utf-8"))
    finally:
        con.close()

    assert _rows(db, "SELECT * FROM task_notifications ORDER BY id") == before
    assert len([r for r in before if r["kind"] == "routine"]) == 1


def test_a_schedule_whose_routine_was_cancelled_is_not_converted_again(tmp_path):
    db, _, _ = _upgrade(tmp_path, FRIDAYS)
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE task_notifications SET closed_at = '2026-10-01T00:00:00Z' "
                    "WHERE kind = 'routine'")
        con.executescript(_MIGRATION.read_text(encoding="utf-8"))
        routines = con.execute("SELECT COUNT(*) FROM task_notifications WHERE kind = 'routine'"
                               ).fetchone()[0]
    finally:
        con.close()
    assert routines == 1
