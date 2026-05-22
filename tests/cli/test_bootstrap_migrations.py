"""Integration tests for the bootstrap migration framework (Section 3c).

These tests would have caught the bug where the schema_version ledger was
stamped to v2 unconditionally while `CREATE TABLE IF NOT EXISTS` in
schema.sql short-circuited on existing DBs -- leaving the live `memory.type`
CHECK constraint at v1 (3 types) while doctor.py reported "v2 matches".

What is covered here:

* `test_fresh_install_stamps_all_versions`
    Empty DB + bootstrap with schema.sql at v2 state. The migration framework
    detects "live DDL already at target" via the guard probe and stamps the
    ledger without running destructive DDL.

* `test_v1_db_migrates_to_v2_preserving_rows`
    Synthesize a v1-shaped DB by hand (narrow CHECK on memory.type plus
    pre-existing rows of type='project'). Run bootstrap. Assert the CHECK was
    widened, the rows survived with original rowids intact, and memory_fts
    still resolves them.

* `test_bootstrap_idempotent_at_current_version`
    Run bootstrap twice on the same DB. The second run must not re-apply the
    migration, must not duplicate ledger rows, and must not corrupt memory_fts.

* `test_bootstrap_aborts_if_migration_file_missing`
    EXPECTED_SCHEMA_VERSION advertises a version with no corresponding
    migration file. Bootstrap must abort non-zero and NOT stamp the ledger
    (the ledger must reflect the real applied state).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_DOCTOR_PY = _REPO_ROOT / "bin" / "cli" / "doctor.py"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_V1_MEMORY_DDL = """
CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE INDEX idx_memory_workspace ON memory(workspace);
CREATE INDEX idx_memory_type ON memory(type);

CREATE VIRTUAL TABLE memory_fts USING fts5(
    workspace UNINDEXED,
    name UNINDEXED,
    description,
    body,
    content='memory',
    content_rowid='rowid'
);

CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;
CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
END;
CREATE TRIGGER memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;
"""


def _run_bootstrap(workspace: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke bootstrap_database.sh with GAIA_DB inside the workspace."""
    tmp_db = workspace / "tmp_gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(tmp_db)
    env["WORKSPACE"] = str(workspace)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _build_v1_db(db_path: Path) -> None:
    """Materialise a DB that looks like it was bootstrapped under v1.

    Specifically:
      * `memory` table with the narrow CHECK constraint (3 types).
      * `workspaces` table seeded with one row so the FK on memory resolves.
      * NO `schema_version` row inserted -- the bootstrap baseline (v1) and
        the migration (v2) are the two ledger entries the test is exercising.
    """
    con = sqlite3.connect(str(db_path))
    try:
        # workspaces is required by memory's FK.
        con.executescript("""
            CREATE TABLE workspaces (
                name     TEXT PRIMARY KEY,
                identity TEXT
            );
            INSERT INTO workspaces (name, identity) VALUES ('test-ws', 'test-ws');
        """)
        # schema_version table -- empty on purpose, the bootstrap will seed v1
        # and run the v1->v2 migration end to end.
        con.executescript("""
            CREATE TABLE schema_version (
                version     INTEGER PRIMARY KEY,
                applied_at  TEXT,
                description TEXT
            );
        """)
        # Memory in v1 shape.
        con.executescript(_V1_MEMORY_DDL)
        # Insert sample rows -- all using v1-legal type='project'.
        for i, name in enumerate(("doc_a", "doc_b", "doc_c", "doc_d", "doc_e"), start=1):
            con.execute(
                "INSERT INTO memory (workspace, name, type, body) VALUES (?, ?, 'project', ?)",
                ("test-ws", name, f"body of {name}"),
            )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBootstrapMigrationFramework(unittest.TestCase):
    """End-to-end coverage of Section 3c (migration loop) of bootstrap.sh."""

    def setUp(self):
        if not _BOOTSTRAP_SH.is_file():
            self.skipTest(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {_SCHEMA_SQL}")
        if not _MIGRATIONS_DIR.is_dir():
            self.skipTest(f"migrations dir not found at {_MIGRATIONS_DIR}")

    # ----- 1. Fresh install path ------------------------------------------

    def test_fresh_install_stamps_all_versions(self):
        """Empty DB + bootstrap: schema.sql creates v2-state tables, ledger is
        stamped to EXPECTED_SCHEMA_VERSION without running destructive DDL."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res = _run_bootstrap(workspace)
            self.assertEqual(
                res.returncode, 0,
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )

            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                versions = [r[0] for r in con.execute(
                    "SELECT version FROM schema_version ORDER BY version"
                )]
                # Expect baseline v1 + every advertised migration through
                # EXPECTED_SCHEMA_VERSION. Fresh install path stamps without
                # running migration DDL because guard probe sees target state.
                self.assertIn(1, versions)
                self.assertIn(2, versions)

                # memory.type CHECK must contain the widened set.
                row = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory'"
                ).fetchone()
                self.assertIsNotNone(row, "memory table missing")
                self.assertIn("'atom'", row[0])
                self.assertIn("'decision'", row[0])
                self.assertIn("'negative'", row[0])

                # Fresh-install stamping should NOT have produced legacy
                # rename artefacts.
                legacy = con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='memory_v1_legacy'"
                ).fetchall()
                self.assertEqual(legacy, [])
            finally:
                con.close()

            # Should be observable from stdout that the migration short-circuited.
            self.assertIn("already at target", res.stdout)

    # ----- 2. Real v1 -> v2 migration path --------------------------------

    def test_v1_db_migrates_to_v2_preserving_rows(self):
        """Synthesize a v1-state DB, run bootstrap, verify rows + rowids
        preserved and CHECK widened."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            _build_v1_db(db)

            # Capture pre-state.
            con = sqlite3.connect(str(db))
            try:
                pre_rows = con.execute(
                    "SELECT rowid, workspace, name, type, body FROM memory ORDER BY rowid"
                ).fetchall()
                pre_check = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory'"
                ).fetchone()[0]
            finally:
                con.close()

            self.assertNotIn(
                "'atom'", pre_check,
                "test setup wrong: v1 DB should have narrow CHECK",
            )
            self.assertEqual(len(pre_rows), 5)

            res = _run_bootstrap(workspace)
            self.assertEqual(
                res.returncode, 0,
                f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )

            con = sqlite3.connect(str(db))
            try:
                # CHECK widened.
                post_check = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory'"
                ).fetchone()[0]
                self.assertIn("'atom'", post_check)
                self.assertIn("'decision'", post_check)
                self.assertIn("'negative'", post_check)

                # Rows + rowids preserved.
                post_rows = con.execute(
                    "SELECT rowid, workspace, name, type, body FROM memory ORDER BY rowid"
                ).fetchall()
                self.assertEqual(pre_rows, post_rows,
                                 "rows or rowids changed during migration")

                # New types are now insertable.
                con.execute(
                    "INSERT INTO memory (workspace, name, type, body) "
                    "VALUES ('test-ws', 'atom_one', 'atom', 'atom body')"
                )
                con.commit()

                # memory_fts still functional -- search for a body keyword.
                fts_hits = con.execute(
                    "SELECT name FROM memory_fts WHERE memory_fts MATCH 'doc_a'"
                ).fetchall()
                self.assertTrue(
                    any("doc_a" in row[0] for row in fts_hits),
                    f"memory_fts lost row 'doc_a' after migration; got {fts_hits!r}",
                )

                # Ledger stamped.
                versions = [r[0] for r in con.execute(
                    "SELECT version FROM schema_version ORDER BY version"
                )]
                self.assertIn(1, versions)
                self.assertIn(2, versions)

                # Migration DDL ran -- the rename-create-copy path leaves
                # an obvious trail in stdout but no leftover legacy table.
                legacy = con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='memory_v1_legacy'"
                ).fetchall()
                self.assertEqual(legacy, [], "migration left rename artefact behind")
            finally:
                con.close()

            self.assertIn("applied successfully", res.stdout)

    # ----- 3. Idempotency at current version ------------------------------

    def test_bootstrap_idempotent_at_current_version(self):
        """Two successive bootstraps on the same DB: second is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res1 = _run_bootstrap(workspace)
            self.assertEqual(res1.returncode, 0, res1.stderr)
            res2 = _run_bootstrap(workspace)
            self.assertEqual(res2.returncode, 0, res2.stderr)

            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                # Same row count in schema_version after both runs.
                count = con.execute(
                    "SELECT COUNT(*) FROM schema_version"
                ).fetchone()[0]
                # v1 + v2 + v3 = 3 rows; no duplicates from re-running.
                self.assertEqual(count, 3,
                                 "second bootstrap duplicated schema_version rows")
            finally:
                con.close()

            # Second run should report "up-to-date" rather than re-stamping.
            self.assertIn("up-to-date", res2.stdout)

    # ----- 4. Failure mode: missing migration file ------------------------

    def test_bootstrap_aborts_if_migration_file_missing(self):
        """If a target version has no migration file, abort and do NOT stamp
        the ledger. The next bootstrap retry must see the same pending state.

        We simulate this by setting up a v1 DB and renaming the v1_to_v2
        migration file out of the way for the duration of the test.
        """
        v1_to_v2 = _MIGRATIONS_DIR / "v1_to_v2.sql"
        v1_to_v2_backup = _MIGRATIONS_DIR / "v1_to_v2.sql.bak"

        if not v1_to_v2.is_file():
            self.skipTest(f"missing fixture migration: {v1_to_v2}")

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            _build_v1_db(db)

            # Move the migration file out of the way. Use shutil.move so the
            # test restores it even on assertion failure (try/finally below).
            shutil.move(str(v1_to_v2), str(v1_to_v2_backup))
            try:
                res = _run_bootstrap(workspace)
                self.assertNotEqual(
                    res.returncode, 0,
                    "bootstrap must abort when migration file is missing; "
                    f"got rc=0\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
                )
                self.assertIn("missing migration file", res.stderr)

                # Ledger must NOT advertise v2 -- the migration never ran.
                con = sqlite3.connect(str(db))
                try:
                    versions = [r[0] for r in con.execute(
                        "SELECT version FROM schema_version"
                    )]
                    self.assertNotIn(
                        2, versions,
                        "bootstrap stamped v2 in ledger despite missing migration",
                    )
                finally:
                    con.close()
            finally:
                # Restore the migration file no matter what.
                shutil.move(str(v1_to_v2_backup), str(v1_to_v2))


# ---------------------------------------------------------------------------
# Parser test for check_schema_ddl_consistency
# ---------------------------------------------------------------------------

class TestDdlCheckParser(unittest.TestCase):
    """Unit test the CHECK-extraction helper used by check_schema_ddl_consistency.

    Kept here (not in test_gaia_doctor) because the bug being defended against
    is co-located with the migration framework: a parser that silently returned
    None on the live DDL would let drift go unreported.
    """

    def test_extracts_widened_check_set(self):
        import sys
        bin_dir = _REPO_ROOT / "bin"
        if str(bin_dir) not in sys.path:
            sys.path.insert(0, str(bin_dir))
        from cli.doctor import _extract_check_values  # noqa: PLC0415

        ddl = (
            "CREATE TABLE memory ("
            "  workspace TEXT, name TEXT, "
            "  type TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative')), "
            "  body TEXT NOT NULL"
            ")"
        )
        self.assertEqual(
            _extract_check_values(ddl, "type"),
            {"project", "user", "feedback", "atom", "decision", "negative"},
        )

    def test_extracts_narrow_check_set(self):
        import sys
        bin_dir = _REPO_ROOT / "bin"
        if str(bin_dir) not in sys.path:
            sys.path.insert(0, str(bin_dir))
        from cli.doctor import _extract_check_values  # noqa: PLC0415

        ddl = (
            "CREATE TABLE memory (type TEXT NOT NULL "
            "CHECK (type IN ('project', 'user', 'feedback')))"
        )
        self.assertEqual(
            _extract_check_values(ddl, "type"),
            {"project", "user", "feedback"},
        )

    def test_returns_none_when_no_check(self):
        import sys
        bin_dir = _REPO_ROOT / "bin"
        if str(bin_dir) not in sys.path:
            sys.path.insert(0, str(bin_dir))
        from cli.doctor import _extract_check_values  # noqa: PLC0415

        self.assertIsNone(
            _extract_check_values("CREATE TABLE foo (type TEXT)", "type"),
        )


if __name__ == "__main__":
    unittest.main()
