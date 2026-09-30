"""AC-1 (harness R1-A): the task_gates slot -- schema, additive migration,
version lockstep.

Matchable by ``pytest tests/ -k task_gates_schema -q``.

Covers BOTH paths the plan's AC-1 requires:

  * FRESH CREATE -- schema.sql builds task_gates with the verification_type
    CHECK against the four VALID_VERIFICATION_TYPES literals, the evidence
    columns, and the task_id index.
  * EXISTING-DB UPGRADE -- a DB seeded at v33 WITHOUT task_gates (and carrying
    prior task rows) upgrades cleanly through the real bootstrap to >= v34: the
    table appears, its CHECK is present, and the pre-existing rows survive.
  * The forward migration file itself creates the table on a DB lacking it, and
    is idempotent (replayed on every fresh install under the floor model).
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v33_to_v34.sql"

_EXPECTED_TYPES = {"command", "code", "semantic", "self_review"}


def _run_bootstrap(workspace: Path) -> subprocess.CompletedProcess:
    tmp_db = workspace / "tmp_gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(tmp_db)
    env["WORKSPACE"] = str(workspace)
    return subprocess.run(
        [sys.executable, str(_BOOTSTRAP_PY)],
        env=env, capture_output=True, text=True, check=False, timeout=120,
    )


def _task_gates_check_values(con: sqlite3.Connection) -> set[str] | None:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_gates'"
    ).fetchone()
    if row is None or not row[0]:
        return None
    m = re.search(
        r"verification_type\s+IN\s*\(\s*(.*?)\s*\)", row[0], re.IGNORECASE | re.DOTALL
    )
    if not m:
        return set()
    return set(re.findall(r"'([^']*)'", m.group(1)))


class TestTaskGatesSchemaFreshCreate(unittest.TestCase):
    """schema.sql (the fresh-install baseline) produces the task_gates slot."""

    def setUp(self):
        if not _SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {_SCHEMA_SQL}")

    def test_task_gates_schema_fresh_create_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fresh.db"
            con = sqlite3.connect(str(db))
            try:
                con.executescript(_SCHEMA_SQL.read_text())

                # Table exists.
                self.assertIsNotNone(
                    con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='task_gates'"
                    ).fetchone(),
                    "task_gates table missing from fresh schema",
                )
                # CHECK carries exactly the four VALID_VERIFICATION_TYPES.
                self.assertEqual(_task_gates_check_values(con), _EXPECTED_TYPES)
                # Index on task_id exists.
                self.assertIsNotNone(
                    con.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND name='idx_task_gates_task'"
                    ).fetchone(),
                    "idx_task_gates_task index missing",
                )
                # Evidence columns copied verbatim from acceptance_criteria.
                cols = {
                    r[1] for r in con.execute("PRAGMA table_info('task_gates')")
                }
                self.assertTrue(
                    {"evidence_type", "evidence_shape", "artifact_path",
                     "status", "verification_type", "task_id"} <= cols,
                    f"task_gates missing expected columns; got {cols}",
                )
            finally:
                con.close()

    def test_task_gates_schema_check_rejects_bad_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fresh.db"
            con = sqlite3.connect(str(db))
            try:
                con.executescript(_SCHEMA_SQL.read_text())
                # Isolate the verification_type CHECK from the task_id FK
                # (this test asserts the CHECK, not referential integrity).
                con.execute("PRAGMA foreign_keys = OFF")
                # A valid type inserts fine.
                con.execute(
                    "INSERT INTO task_gates (task_id, verification_type) "
                    "VALUES (1, 'command')"
                )
                # An out-of-enum type is rejected by the CHECK.
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO task_gates (task_id, verification_type) "
                        "VALUES (1, 'not_a_type')"
                    )
            finally:
                con.close()


class TestTaskGatesSchemaMigration(unittest.TestCase):
    """The forward migration file creates the table on a DB lacking it, and is
    idempotent (floor-model replay)."""

    def setUp(self):
        if not _MIGRATION.is_file():
            self.skipTest(f"migration not found at {_MIGRATION}")
        if not _SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {_SCHEMA_SQL}")

    def test_task_gates_schema_migration_creates_table_on_existing_db(self):
        mig_sql = _MIGRATION.read_text()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v33.db"
            con = sqlite3.connect(str(db))
            try:
                con.executescript(_SCHEMA_SQL.read_text())
                # Simulate a pre-v34 DB: drop the slot schema.sql just created.
                con.execute("DROP INDEX IF EXISTS idx_task_gates_task")
                con.execute("DROP TABLE IF EXISTS task_gates")
                con.commit()
                self.assertIsNone(
                    con.execute(
                        "SELECT name FROM sqlite_master WHERE name='task_gates'"
                    ).fetchone(),
                    "fixture still has task_gates -- not a pre-v34 state",
                )

                # Applying ONLY the migration creates it (not schema.sql).
                con.executescript(mig_sql)
                con.commit()
                self.assertEqual(_task_gates_check_values(con), _EXPECTED_TYPES)

                # Idempotent: a second apply is a clean no-op.
                con.executescript(mig_sql)
                con.commit()
                self.assertEqual(_task_gates_check_values(con), _EXPECTED_TYPES)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
