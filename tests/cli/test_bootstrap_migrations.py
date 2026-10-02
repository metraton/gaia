"""Integration tests for the bootstrap migration framework under the schema
FLOOR model (the migration chain of bootstrap_database.py).

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
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_DOCTOR_PY = _REPO_ROOT / "bin" / "cli" / "doctor.py"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_floor() -> int:
    """Parse SCHEMA_FLOOR=N from bootstrap_database.py."""
    text = _BOOTSTRAP_PY.read_text()
    m = re.search(r"^\s*SCHEMA_FLOOR\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert m is not None, "SCHEMA_FLOOR not found in bootstrap_database.py"
    return int(m.group(1))


def _read_expected_version() -> int:
    """Parse EXPECTED_SCHEMA_VERSION=N from bin/cli/doctor.py."""
    text = _DOCTOR_PY.read_text()
    m = re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert m is not None, "EXPECTED_SCHEMA_VERSION not found in doctor.py"
    return int(m.group(1))


def _run_bootstrap(workspace: Path, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the canonical Python bootstrapper with GAIA_DB inside the workspace.

    This is the path `gaia install`/`gaia update`/the lazy bootstrap all use
    (see bin/cli/install.py::_run_bootstrap). It needs neither `bash` nor the
    `sqlite3` CLI, so these tests hold on a machine that has only the suite's
    own toolchain.
    """
    tmp_db = workspace / "tmp_gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(tmp_db)
    env["WORKSPACE"] = str(workspace)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(_BOOTSTRAP_PY)],
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


def _build_above_expected_db(db_path: Path, version: int) -> None:
    """Materialise a full, valid DB whose schema_version ledger is stamped at
    *version* -- used to simulate a DB migrated FORWARD by a NEWER Gaia than the
    code under test. Applies the current schema.sql (so every table/trigger the
    later bootstrap sections touch exists), then rewrites the ledger to a single
    row at *version*.
    """
    schema_sql = _SCHEMA_SQL.read_text()
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(schema_sql)
        con.execute("DELETE FROM schema_version")
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, '2026-01-01T00:00:00Z', 'synthetic newer-than-code DB')",
            (version,),
        )
        con.commit()
    finally:
        con.close()


def _build_v26_pre_contract_id_db(db_path: Path) -> None:
    """Materialise an EXISTING pre-`contract_id` DB stamped at v26.

    Strategy: apply the full current schema.sql, then roll the
    agent_contract_handoffs table back to its pre-v28 shape by dropping the
    v28 `contract_id` column and its UNIQUE index, and stamp the ledger at v26.

    This reproduces the EXACT real-world failure state the fresh-DB suite never
    exercises: agent_contract_handoffs EXISTS (from v26) but has no
    `contract_id` column, so bootstrap Section 2 -- which applies schema.sql
    unconditionally BEFORE the migration ledger (Section 3c) -- hits
    `CREATE UNIQUE INDEX ... ON agent_contract_handoffs(contract_id)`
    (schema.sql ~line 985) against a table lacking that column and aborts with
    "no such column: contract_id".
    """
    schema_sql = _SCHEMA_SQL.read_text()
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(schema_sql)
        # Roll agent_contract_handoffs back to its pre-v28 (v26) shape.
        con.execute("DROP INDEX IF EXISTS idx_agent_contract_handoffs_contract_id")
        con.execute("ALTER TABLE agent_contract_handoffs DROP COLUMN contract_id")
        # schema.sql seeds no schema_version rows; stamp this as an existing v26 DB.
        con.execute("DELETE FROM schema_version")
        con.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (26, '2026-01-01T00:00:00Z', 'synthetic existing v26 DB')"
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpgradeExistingDbToV28(unittest.TestCase):
    """Regression: an EXISTING pre-`contract_id` (v26) DB must upgrade cleanly
    to v28 through bootstrap.

    Guards the install path the fresh-DB suite never exercised. bootstrap
    applies schema.sql (Section 2) BEFORE the migration ledger (Section 3c), so
    an index in schema.sql on a migration-added column (v28's
    agent_contract_handoffs.contract_id) aborted on an existing table lacking
    that column. The Section 1.5 pre-schema ADD COLUMN reconciliation adds the
    column before schema.sql's index build.
    """

    def setUp(self):
        if not _BOOTSTRAP_PY.is_file():
            self.skipTest(f"bootstrap script not found at {_BOOTSTRAP_PY}")
        if not _SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {_SCHEMA_SQL}")
        if sqlite3.sqlite_version_info < (3, 35, 0):
            self.skipTest(
                "ALTER TABLE DROP COLUMN (used to build the fixture) requires "
                f"SQLite >= 3.35; have {sqlite3.sqlite_version}"
            )

    def _assert_v28_consistent(self, db: Path) -> None:
        con = sqlite3.connect(str(db))
        try:
            # Floor check (per scripts/migrations/README.md section 2): the DB
            # must reach AT LEAST v28 -- bootstrap upgrades all the way to the
            # CLI's EXPECTED_SCHEMA_VERSION (>= 28 and rising as migrations are
            # added), so a `== 28` point-check breaks on every later bump. The
            # v28-SPECIFIC guarantee this test defends (the contract_id column +
            # its UNIQUE index) is asserted below and is unaffected by the floor.
            self.assertGreaterEqual(
                con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                28,
                "ledger did not reach at least v28 after upgrade",
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM pragma_table_info('agent_contract_handoffs') "
                    "WHERE name='contract_id'"
                ).fetchone()[0],
                1,
                "contract_id column missing after upgrade",
            )
            idx = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='idx_agent_contract_handoffs_contract_id'"
            ).fetchone()
            self.assertIsNotNone(idx, "contract_id index missing after upgrade")
            self.assertIn(
                "UNIQUE", idx[0].upper(),
                "contract_id index is not UNIQUE after upgrade",
            )
        finally:
            con.close()

    def test_existing_v26_db_upgrades_to_v28(self):
        """Existing v26 DB lacking contract_id upgrades to v28 with no error and
        a consistent contract_id column + UNIQUE index."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            _build_v26_pre_contract_id_db(db)

            # Precondition: fixture is the real failure state.
            con = sqlite3.connect(str(db))
            try:
                self.assertEqual(
                    con.execute(
                        "SELECT COUNT(*) FROM pragma_table_info('agent_contract_handoffs') "
                        "WHERE name='contract_id'"
                    ).fetchone()[0],
                    0,
                    "fixture already has contract_id -- not a pre-v28 state",
                )
                self.assertEqual(
                    con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                    26,
                )
            finally:
                con.close()

            res = _run_bootstrap(workspace)
            self.assertEqual(
                res.returncode, 0,
                f"upgrade bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}",
            )
            self._assert_v28_consistent(db)

class TestBootstrapFloorModel(unittest.TestCase):
    """End-to-end coverage of Section 3b/3c under the floor model."""

    @pytest.fixture(autouse=True)
    def _fresh_install(self, bootstrapped_db_template):
        """The session's real bootstrap output, standing in for a first run."""
        self.fresh_install = bootstrapped_db_template

    def setUp(self):
        if not _BOOTSTRAP_PY.is_file():
            self.skipTest(f"bootstrap script not found at {_BOOTSTRAP_PY}")
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

            # The run replays the chain from the floor and seals at EXPECTED.
            self.assertIn(
                f"chain v{self.floor}..v{self.expected}: applied and sealed "
                f"at v{self.expected}",
                res.stdout,
            )

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
            copy_bootstrapped_db(self.fresh_install, workspace / "tmp_gaia.db")
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


class TestSchemaDirectionGuard(unittest.TestCase):
    """The drift-free direction guard: an install must REFUSE to run code older
    than the live DB (DB schema_version > code's EXPECTED_SCHEMA_VERSION).

    This is the reverse of the forward-migration case: bootstrap only migrates
    FORWARD, and the reverse direction (a DB migrated by a newer Gaia than the
    code being installed) was previously unguarded -- the `else` branch logged
    "up-to-date" and the install "succeeded", leaving stale code to read a newer
    schema. That is the exact drift that broke `gaia contract finalize`. Both
    bootstrap path must:
      * exit non-zero,
      * name the direction ("NEWER than ... expects"),
      * leave the DB ledger UNTOUCHED (no clobber).
    """

    def setUp(self):
        if not _SCHEMA_SQL.is_file():
            self.skipTest(f"schema.sql not found at {_SCHEMA_SQL}")
        self.expected = _read_expected_version()

    def _assert_refused_and_untouched(self, res, db: Path, stamped: int) -> None:
        self.assertNotEqual(
            res.returncode, 0,
            "bootstrap must refuse code older than the DB; got rc=0\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}",
        )
        self.assertIn("NEWER than", res.stderr)
        con = sqlite3.connect(str(db))
        try:
            versions = [r[0] for r in con.execute("SELECT version FROM schema_version")]
        finally:
            con.close()
        self.assertEqual(
            versions, [stamped],
            "guard clobbered the ledger of a DB it should have refused to touch",
        )

    def test_py_bootstrap_refuses_newer_db(self):
        if not _BOOTSTRAP_PY.is_file():
            self.skipTest(f"bootstrap_database.py not found at {_BOOTSTRAP_PY}")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            db = workspace / "tmp_gaia.db"
            newer = self.expected + 1
            _build_above_expected_db(db, newer)
            res = _run_bootstrap(workspace)
            self._assert_refused_and_untouched(res, db, newer)

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


_HARNESS = "/w/harness/Auto-claude-code-research-in-sleep"
_DUPLICATE = "/w/github-repos/_duplicados/auto-claude-sleep"
_REMOTE_IDENTITY = "github.com/metraton/auto-claude-code-research-in-sleep"


def _apply_v64(db: Path) -> None:
    _apply_migration(db, "v63_to_v64.sql")


def _apply_migration(db: Path, filename: str) -> None:
    """Run one migration the way the bootstrap does: statement by statement, one transaction."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    import migration_guard

    con = sqlite3.connect(str(db))
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        sql = (_MIGRATIONS_DIR / filename).read_text(encoding="utf-8")
        for statement in migration_guard.split_statements(sql):
            con.execute(statement)
        con.execute("COMMIT")
    finally:
        con.close()


@pytest.fixture()
def duplicate_clone_pair(bootstrapped_db_template, tmp_path):
    """A v63 database holding the real duplicate pair as two rows keyed by git-common-dir."""
    tmp_db = copy_bootstrapped_db(bootstrapped_db_template, tmp_path / "gaia.db")
    con = sqlite3.connect(str(tmp_db))
    try:
        con.executemany(
            "INSERT INTO projects (workspace, name, remote_url, path, status, "
            "project_identity, description) VALUES ('ws', ?, ?, ?, 'active', ?, ?)",
            [
                ("auto-claude-sleep",
                 "git@github.com:Metraton/Auto-claude-code-research-in-sleep.git",
                 _DUPLICATE, f"{_DUPLICATE}/.git", None),
                ("Auto-claude-code-research-in-sleep",
                 "https://github.com/metraton/auto-claude-code-research-in-sleep",
                 _HARNESS, f"{_HARNESS}/.git", "research harness"),
                ("local-only", None, "/w/local-only", "/w/local-only/.git", None),
            ],
        )
        con.execute(
            "INSERT INTO apps (workspace, project, name) VALUES ('ws', 'auto-claude-sleep', 'api')"
        )
        con.executemany(
            "INSERT INTO memory (workspace, name, type, body, project_ref, initiative) "
            "VALUES ('ws', ?, 'project', 'note', ?, ?)",
            [
                ("note-duplicate", f"{_DUPLICATE}/.git", "auto_claude_sleep"),
                ("note-harness", f"{_HARNESS}/.git", "auto_claude_code_research_in_sleep"),
                ("note-unscoped", f"{_DUPLICATE}/.git", None),
                ("note-local", "/w/local-only/.git", "local_only"),
            ],
        )
        con.commit()
    finally:
        con.close()
    return tmp_db


def _query(db: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_v64_folds_the_duplicate_clone_pair_into_one_project_with_a_copy(duplicate_clone_pair):
    _apply_v64(duplicate_clone_pair)

    assert _query(
        duplicate_clone_pair,
        "SELECT name, project_identity, description FROM projects ORDER BY name",
    ) == [
        ("Auto-claude-code-research-in-sleep", _REMOTE_IDENTITY, "research harness"),
        ("local-only", "/w/local-only/.git", None),
    ]
    assert _query(
        duplicate_clone_pair, "SELECT project, scope, key FROM project_facets"
    ) == [("Auto-claude-code-research-in-sleep", "copy", _DUPLICATE)]
    assert _query(duplicate_clone_pair, "SELECT COUNT(*) FROM apps") == [(0,)]


def test_v64_keeps_every_memory_note_attached_to_its_project(duplicate_clone_pair):
    from gaia.store.writer import canonical_project_key, initiative_from_project_ref

    _apply_v64(duplicate_clone_pair)

    notes = _query(
        duplicate_clone_pair,
        "SELECT name, project_ref, initiative FROM memory ORDER BY name",
    )
    assert [(name, ref) for name, ref, _ in notes] == [
        ("note-duplicate", _REMOTE_IDENTITY),
        ("note-harness", _REMOTE_IDENTITY),
        ("note-local", "/w/local-only/.git"),
        ("note-unscoped", _REMOTE_IDENTITY),
    ]
    project_key = initiative_from_project_ref(_REMOTE_IDENTITY)
    assert {
        name: canonical_project_key(ref, initiative) for name, ref, initiative in notes
    } == {
        "note-duplicate": project_key,
        "note-harness": project_key,
        "note-local": "local_only",
        "note-unscoped": project_key,
    }


def test_v65_adds_a_nullable_brief_project_and_leaves_existing_briefs_untouched(
    bootstrapped_db_template, tmp_path,
):
    db = copy_bootstrapped_db(bootstrapped_db_template, tmp_path / "gaia.db")
    con = sqlite3.connect(str(db))
    try:
        con.execute("ALTER TABLE briefs DROP COLUMN project")
        con.execute("INSERT INTO workspaces (name) VALUES ('ws')")
        con.execute(
            "INSERT INTO briefs (workspace, name, status, title, created_at, updated_at) "
            "VALUES ('ws', 'roadmap', 'open', 'Roadmap', '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z')"
        )
        con.commit()
    finally:
        con.close()
    before = _query(db, "SELECT * FROM briefs")

    _apply_migration(db, "v64_to_v65.sql")

    column = [c for c in _query(db, "PRAGMA table_info(briefs)") if c[1] == "project"]
    assert [(c[2], c[3]) for c in column] == [("TEXT", 0)]
    assert _query(db, "SELECT * FROM briefs") == [row + (None,) for row in before]


if __name__ == "__main__":
    unittest.main()
