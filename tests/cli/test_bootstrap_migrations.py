"""Integration tests for the bootstrap migration framework under the schema
FLOOR model (Section 3b/3c of bootstrap_database.sh).

The historical v1->v17 migration chain was collapsed into a floor (v18). The
bootstrap script no longer seeds v1 and walks the chain; instead it:

  * stamps the ledger at the FLOOR directly on a fresh DB (schema.sql already
    produced the floor shape),
  * refuses any DB below the floor (in-place upgrade unsupported),
  * applies forward migrations (v{FLOOR+1}+) for DBs behind EXPECTED.

What is covered here:

* `test_fresh_install_stamps_floor`
    Empty DB + bootstrap: schema.sql builds the floor shape and the ledger is
    stamped at exactly the floor (no v1, no chain walk).

* `test_db_below_floor_is_rejected`
    Synthesize a DB stamped at a pre-floor version. Bootstrap must abort
    non-zero with a clear "below the supported floor" message and must NOT
    silently advance the ledger.

* `test_bootstrap_idempotent_at_floor`
    Run bootstrap twice on the same DB. The second run must not duplicate
    ledger rows and must report "up-to-date".

* `TestDdlCheckParser`
    Unit tests for the CHECK-extraction helper used by
    check_schema_ddl_consistency (co-located because the bug it defends
    against lives in the migration/drift area).
"""

from __future__ import annotations

import os
import re
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

def _read_floor() -> int:
    """Parse SCHEMA_FLOOR=N from bootstrap_database.sh."""
    text = _BOOTSTRAP_SH.read_text()
    m = re.search(r"^\s*SCHEMA_FLOOR\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert m is not None, "SCHEMA_FLOOR not found in bootstrap_database.sh"
    return int(m.group(1))


def _read_expected_version() -> int:
    """Parse EXPECTED_SCHEMA_VERSION=N from bin/cli/doctor.py."""
    text = _DOCTOR_PY.read_text()
    m = re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert m is not None, "EXPECTED_SCHEMA_VERSION not found in doctor.py"
    return int(m.group(1))


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


def _build_below_floor_db(db_path: Path, version: int) -> None:
    """Materialise a minimal DB whose schema_version ledger is below the floor.

    We only need the schema_version table populated -- Section 3b reads
    MAX(version) before any further work, so the below-floor rejection fires
    before the rest of the schema is touched.
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            "CREATE TABLE schema_version ("
            "    version     INTEGER PRIMARY KEY,"
            "    applied_at  TEXT NOT NULL,"
            "    description TEXT"
            ");"
        )
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'synthetic pre-floor')",
            (version,),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBootstrapFloorModel(unittest.TestCase):
    """End-to-end coverage of Section 3b/3c under the floor model."""

    def setUp(self):
        if not _BOOTSTRAP_SH.is_file():
            self.skipTest(f"bootstrap script not found at {_BOOTSTRAP_SH}")
        if not _SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {_SCHEMA_SQL}")
        self.floor = _read_floor()
        self.expected = _read_expected_version()

    # ----- 1. Fresh install lands at the floor ----------------------------

    def test_fresh_install_stamps_floor(self):
        """Empty DB + bootstrap: ledger stamped at floor then advanced to
        EXPECTED_SCHEMA_VERSION via forward migrations, no v1 baseline seed."""
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
                # Floor is present; the obsolete v1 baseline must NOT be.
                self.assertIn(self.floor, versions)
                self.assertNotIn(1, versions,
                                 "fresh install seeded the obsolete v1 baseline")
                # MAX version must reach EXPECTED_SCHEMA_VERSION: fresh install
                # seeds at FLOOR then bootstrap walks forward migrations to EXPECTED.
                self.assertEqual(max(versions), self.expected)

                # memory.type CHECK must contain the widened set (floor shape).
                row = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory'"
                ).fetchone()
                self.assertIsNotNone(row, "memory table missing")
                self.assertIn("'atom'", row[0])
                self.assertIn("'decision'", row[0])
                self.assertIn("'negative'", row[0])
            finally:
                con.close()

            # stdout should reflect the floor baseline path, not a chain walk.
            self.assertIn(f"floor (v{self.floor})", res.stdout)

    # ----- 2. Below-floor DB is rejected ----------------------------------

    def test_db_below_floor_is_rejected(self):
        """A DB stamped below the floor must abort the bootstrap with a clear
        message and must NOT be silently advanced."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            below = self.floor - 1
            _build_below_floor_db(db, below)

            res = _run_bootstrap(workspace)
            self.assertNotEqual(
                res.returncode, 0,
                "bootstrap must abort on a below-floor DB; "
                f"got rc=0\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )
            self.assertIn("below the supported floor", res.stderr)

            # Ledger must NOT have been advanced to the floor.
            con = sqlite3.connect(str(db))
            try:
                versions = [r[0] for r in con.execute(
                    "SELECT version FROM schema_version"
                )]
                self.assertEqual(
                    versions, [below],
                    "bootstrap mutated the ledger of a below-floor DB it should "
                    "have refused to touch",
                )
            finally:
                con.close()

    # ----- 3. Idempotency at the floor ------------------------------------

    def test_bootstrap_idempotent_at_floor(self):
        """Two successive bootstraps on the same DB: second run is a no-op
        and adds no duplicate schema_version rows."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            res1 = _run_bootstrap(workspace)
            self.assertEqual(res1.returncode, 0, res1.stderr)
            res2 = _run_bootstrap(workspace)
            self.assertEqual(res2.returncode, 0, res2.stderr)

            db = workspace / "tmp_gaia.db"
            con = sqlite3.connect(str(db))
            try:
                count = con.execute(
                    "SELECT COUNT(*) FROM schema_version"
                ).fetchone()[0]
                # Floor model: one baseline row (FLOOR) plus one row per forward
                # migration (FLOOR+1 .. EXPECTED).  Idempotency means the second
                # bootstrap run must NOT duplicate any of these rows.
                expected_rows = 1 + (self.expected - self.floor)
                self.assertEqual(
                    count, expected_rows,
                    f"expected {expected_rows} schema_version row(s) "
                    f"(floor={self.floor}, expected={self.expected}), got {count}",
                )
            finally:
                con.close()

            # Second run sees a DB already at or above the floor.
            self.assertIn(f">= floor v{self.floor}", res2.stdout)


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

    def test_extracts_from_multi_table_schema(self):
        """Regression: when schema.sql has multiple tables with a 'type' CHECK,
        the table= parameter must narrow the search to the correct table.

        Before the fix, _extract_check_values ran re.search on the full schema
        text and always returned the first match -- evidence.type values leaked
        into the memory.type comparison, producing a false DDL drift error.
        """
        import sys
        bin_dir = _REPO_ROOT / "bin"
        if str(bin_dir) not in sys.path:
            sys.path.insert(0, str(bin_dir))
        from cli.doctor import _extract_check_values  # noqa: PLC0415

        # Minimal two-table schema that reproduces the corruption symptom.
        schema = (
            "CREATE TABLE IF NOT EXISTS evidence ("
            "  id INTEGER PRIMARY KEY,"
            "  type TEXT NOT NULL CHECK (type IN ('text', 'file', 'command_output', 'url', 'screenshot'))"
            ");\n"
            "CREATE TABLE IF NOT EXISTS memory ("
            "  workspace TEXT NOT NULL,"
            "  type TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative'))"
            ");"
        )

        # Without table= the parser returns the first match (evidence values).
        first_match = _extract_check_values(schema, "type")
        self.assertEqual(first_match, {"text", "file", "command_output", "url", "screenshot"})

        # With table="memory" the parser must return the memory values.
        memory_values = _extract_check_values(schema, "type", table="memory")
        self.assertEqual(
            memory_values,
            {"project", "user", "feedback", "atom", "decision", "negative"},
        )

        # With table="evidence" the parser must return the evidence values.
        evidence_values = _extract_check_values(schema, "type", table="evidence")
        self.assertEqual(
            evidence_values,
            {"text", "file", "command_output", "url", "screenshot"},
        )


if __name__ == "__main__":
    unittest.main()
