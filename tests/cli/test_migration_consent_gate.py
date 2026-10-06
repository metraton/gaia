"""The migration consent gate: a migration whose effects reach existing user
data cannot be applied unattended by `scripts/bootstrap_database.py`.

Section 3c applies every pending migration file it finds in the source tree on
any invocation that bootstraps. `v49_to_v50.sql` was the first that also touched
DATA, and the runner did not distinguish -- committing the file was enough for
the next arbitrary CLI call to capture and erase a counter on 1359 live curated
rows. These tests hold the property that closes that: a structure-only chain
passes alone after a backup, a chain reaching data stops once for the whole
chain (D68), and the real file that caused the incident stops.

Every case builds its own database under tmp_path. None of them reads or writes
the user's ~/.gaia/gaia.db.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import migration_guard  # noqa: E402


_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "scripts" / "migrations"
_DOCTOR_PY = _REPO_ROOT / "bin" / "cli" / "doctor.py"


def _expected_version() -> int:
    match = re.search(
        r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", _DOCTOR_PY.read_text(), re.MULTILINE
    )
    assert match, "EXPECTED_SCHEMA_VERSION not found in doctor.py"
    return int(match.group(1))


def _live_db(tmp: Path, version: int, memory_rows: int) -> Path:
    """A database that looks like a user's: real schema, real rows, stamped
    one version behind so exactly one migration is pending."""
    db = tmp / "live.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    con.execute("DELETE FROM schema_version")
    con.execute(
        "INSERT INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
        (version, "2026-01-01T00:00:00Z", "test fixture"),
    )
    con.execute("INSERT INTO workspaces (name, identity) VALUES ('me', 'me')")
    for i in range(memory_rows):
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body) "
            "VALUES (?, ?, 'project', 'fixture', 'fixture')",
            ("me", f"fixture_row_{i}"),
        )
    con.commit()
    con.close()
    return db


def _stage_runner(tmp: Path, expected: int) -> Path:
    """Copy the real runner into a sandbox that owns its own migrations dir.

    `MIG_DIR` and `DOCTOR_PY` are both resolved from the script's own location,
    so staging the script is what lets a test drive the runner over migrations
    it controls -- without adding a directory override to production and
    without ever writing a synthetic migration into the source tree, where a
    real bootstrap would find it.
    """
    scripts = tmp / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("bootstrap_database.py", "migration_guard.py"):
        (scripts / name).write_text(
            (_REPO_ROOT / "scripts" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (scripts / "migrations").mkdir(exist_ok=True)
    doctor = tmp / "bin" / "cli"
    doctor.mkdir(parents=True, exist_ok=True)
    (doctor / "doctor.py").write_text(
        f"EXPECTED_SCHEMA_VERSION = {expected}\n", encoding="utf-8"
    )
    return scripts / "bootstrap_database.py"


def _run_bootstrap(runner: Path, db: Path, consent: str | None = None):
    env = os.environ.copy()
    env["GAIA_DB"] = str(db)
    env["SCHEMA_FILE"] = str(_SCHEMA_SQL)
    argv = [sys.executable, str(runner)]
    if consent is not None:
        argv += [migration_guard.CONSENT_FLAG, consent]
    return subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestStatementClassification(unittest.TestCase):
    """The classification reads the SQL, so an author cannot omit it."""

    LOADED = {"memory": 1359, "task_gates": 12}

    def test_structure_forms_reach_nothing(self):
        for sql in (
            "CREATE TABLE IF NOT EXISTS thing (a TEXT);",
            "ALTER TABLE memory ADD COLUMN created_at TEXT;",
            "CREATE INDEX IF NOT EXISTS idx_x ON memory(name);",
            "DROP INDEX IF EXISTS idx_x;",
            "DROP TRIGGER IF EXISTS memory_au;",
            "INSERT INTO thing (a) SELECT name FROM memory;",
        ):
            with self.subTest(sql=sql):
                self.assertEqual((), migration_guard.scan(sql, self.LOADED))

    def test_row_reaching_forms_are_found_with_their_table(self):
        cases = {
            "UPDATE memory SET deliberate_count = 0;": ("UPDATE", "memory"),
            "DELETE FROM memory WHERE name = 'x';": ("DELETE", "memory"),
            "DROP TABLE memory;": ("DROP TABLE", "memory"),
            "ALTER TABLE memory RENAME TO memory_old;": ("ALTER TABLE RENAME", "memory"),
            "ALTER TABLE memory DROP COLUMN body;": ("ALTER TABLE DROP", "memory"),
            "INSERT OR REPLACE INTO memory (name) VALUES ('x');": (
                "INSERT OR REPLACE",
                "memory",
            ),
        }
        for sql, (verb, table) in cases.items():
            with self.subTest(sql=sql):
                reaches = migration_guard.scan(sql, self.LOADED)
                self.assertEqual(1, len(reaches))
                self.assertEqual(verb, reaches[0].verb)
                self.assertEqual(table, reaches[0].table)
                self.assertEqual(1359, reaches[0].rows)

    def test_a_schema_qualified_table_resolves_to_the_same_census_key(self):
        reaches = migration_guard.scan("DELETE FROM main.memory;", self.LOADED)
        self.assertEqual(1, len(reaches))
        self.assertEqual("memory", reaches[0].table)
        self.assertEqual(1359, reaches[0].rows)

    def test_unrecognised_statement_is_gated_not_assumed_safe(self):
        reaches = migration_guard.scan("FROBNICATE memory HARDER;", self.LOADED)
        self.assertEqual(1, len(reaches))
        self.assertEqual("UNRECOGNISED", reaches[0].verb)
        self.assertEqual(sum(self.LOADED.values()), reaches[0].rows)

    def test_prose_about_a_mutation_is_not_a_mutation(self):
        sql = (
            "-- This migration replaces the old UPDATE memory backfill and the\n"
            "-- DELETE FROM memory sweep that preceded it.\n"
            "/* DROP TABLE memory; was considered and rejected. */\n"
            "CREATE INDEX IF NOT EXISTS idx_y ON memory(name);\n"
        )
        self.assertEqual((), migration_guard.scan(sql, self.LOADED))

    def test_creating_a_trigger_is_structural_whatever_its_body_runs(self):
        """A trigger body runs on future writes, never on rows that exist when
        the migration applies, so creating one reaches no existing row."""
        for body in (
            "INSERT INTO memory_fts(rowid, name) VALUES (new.rowid, new.name);",
            "DELETE FROM memory WHERE name = new.name;",
        ):
            sql = f"CREATE TRIGGER t AFTER INSERT ON memory BEGIN {body} END;"
            with self.subTest(body=body):
                self.assertEqual((), migration_guard.scan(sql, self.LOADED))

    def test_a_statement_after_a_trigger_is_still_classified(self):
        sql = (
            "CREATE TRIGGER t AFTER INSERT ON memory BEGIN SELECT 1; END;\n"
            "UPDATE memory SET body = 'x';\n"
        )
        self.assertEqual(
            [("UPDATE", "memory")],
            [(r.verb, r.table) for r in migration_guard.scan(sql, self.LOADED)],
        )

    def test_the_consent_names_the_chain_span(self):
        self.assertEqual("v49..v58", migration_guard.chain_label(49, 58))
        self.assertEqual(
            "gaia migrate apply --consent-chain v49..v58",
            migration_guard.consent_command("v49..v58"),
        )


class TestRealCorpusIsClassifiedPrecisely(unittest.TestCase):
    """Against a census in which every table is populated, the shipped corpus
    splits cleanly -- and nothing in it is unrecognised."""

    def _loaded_census(self) -> dict[str, int]:
        con = sqlite3.connect(":memory:")
        con.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
        census = {name: 1 for name in migration_guard.take_census(con)}
        con.close()
        return census

    def test_no_shipped_migration_is_unrecognised(self):
        census = self._loaded_census()
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            unrecognised = [
                reach
                for reach in migration_guard.scan(path.read_text(), census)
                if reach.verb == "UNRECOGNISED"
            ]
            self.assertEqual(
                [], unrecognised, f"{path.name} produced an unrecognised statement"
            )

    def test_v49_to_v50_is_data_reaching_and_names_the_memory_table(self):
        census = self._loaded_census()
        reaches = migration_guard.scan(
            (_MIGRATIONS_DIR / "v49_to_v50.sql").read_text(), census
        )
        self.assertEqual(
            [("UPDATE", "memory")], [(r.verb, r.table) for r in reaches]
        )

    def test_the_telemetry_and_trigger_migrations_stay_structure_only(self):
        census = self._loaded_census()
        for name in ("v47_to_v48.sql", "v48_to_v49.sql", "v44_to_v45.sql"):
            with self.subTest(name=name):
                self.assertEqual(
                    (),
                    migration_guard.scan((_MIGRATIONS_DIR / name).read_text(), census),
                )


class TestFreshDatabaseIsNeverGated(unittest.TestCase):
    """The comfort that makes the system usable is preserved by construction:
    a database with nothing in it has nothing to lose."""

    def test_empty_census_lets_a_data_reaching_migration_through(self):
        sql = (_MIGRATIONS_DIR / "v49_to_v50.sql").read_text()
        verdict = migration_guard.assess("v49_to_v50", sql, {})
        self.assertEqual((), verdict.reaches)

    def test_a_table_created_during_this_run_is_not_at_risk(self):
        census = {"memory": 1359}
        verdict = migration_guard.assess(
            "v50_to_v51", "UPDATE brand_new_table SET x = 0;", census
        )
        self.assertEqual((), verdict.reaches)

    def test_a_real_fresh_install_applies_the_whole_chain_with_no_consent(self):
        """The install path must never stop asking for a consent nobody can give."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fresh.db"
            env = os.environ.copy()
            env["GAIA_DB"] = str(db)
            result = subprocess.run(
                [sys.executable, str(_BOOTSTRAP_PY)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertNotIn("BLOCKED", result.stderr)

            con = sqlite3.connect(db)
            self.assertEqual(
                _expected_version(),
                con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
            )
            # Two data-reaching files ran unattended here, and each leaves its
            # own witness: v49_to_v50 added the kernel axis and filled the
            # capture table, v50_to_v51 dropped that table again. Presence of
            # the column and absence of the table is the pair -- the table alone
            # stopped being a witness the moment v51 removed it.
            self.assertIn(
                "kernel_count",
                [row[1] for row in con.execute("PRAGMA table_info(memory)")],
            )
            self.assertEqual(
                0,
                con.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE name='memory_deliberate_capture_v50'"
                ).fetchone()[0],
            )
            con.close()


class TestTheGateBitesEndToEnd(unittest.TestCase):
    """The three demonstrations, driven through the real runner."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.expected = _expected_version()
        self.runner = _stage_runner(self.tmp, self.expected)
        self.migrations = self.runner.parent / "migrations"

    def tearDown(self):
        self._tmp.cleanup()

    def _stage(self, name: str, body: str):
        (self.migrations / name).write_text(body, encoding="utf-8")

    def _copy_real(self, name: str):
        self._stage(name, (_MIGRATIONS_DIR / name).read_text(encoding="utf-8"))

    def test_structure_only_migration_still_applies_unattended(self):
        db = _live_db(self.tmp, self.expected - 1, memory_rows=1359)
        self._stage(
            f"v{self.expected - 1}_to_v{self.expected}.sql",
            "CREATE TABLE IF NOT EXISTS gate_demo_structure (a TEXT);\n"
            "CREATE INDEX IF NOT EXISTS idx_gate_demo ON gate_demo_structure(a);\n",
        )
        result = _run_bootstrap(self.runner, db)
        print("\n".join(l for l in result.stdout.splitlines() if "migration" in l))

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("BLOCKED", result.stderr)
        backups = list((db.parent / "backups").glob("*.db"))
        self.assertEqual(1, len(backups), "an existing database is backed up first")
        with sqlite3.connect(backups[0]) as saved:
            self.assertEqual(
                self.expected - 1,
                saved.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
            )

        con = sqlite3.connect(db)
        self.assertEqual(
            self.expected,
            con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
        )
        self.assertEqual(
            1,
            con.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='gate_demo_structure'"
            ).fetchone()[0],
        )
        con.close()

    def test_data_reaching_migration_is_stopped_and_changes_nothing(self):
        db = _live_db(self.tmp, self.expected - 1, memory_rows=1359)
        self._stage(
            f"v{self.expected - 1}_to_v{self.expected}.sql",
            "UPDATE memory SET description = 'clobbered';\n",
        )
        result = _run_bootstrap(self.runner, db)
        print(result.stderr)

        self.assertEqual(1, result.returncode)
        self.assertIn("BLOCKED", result.stderr)
        self.assertIn("1359 row(s) at risk", result.stderr)
        label = migration_guard.chain_label(self.expected - 1, self.expected)
        self.assertIn(
            f"gaia migrate apply --consent-chain {label}", result.stderr
        )
        self.assertFalse((db.parent / "backups").exists(), "a refusal writes nothing")

        con = sqlite3.connect(db)
        self.assertEqual(
            self.expected - 1,
            con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
        )
        self.assertEqual(
            0,
            con.execute(
                "SELECT COUNT(*) FROM memory WHERE description = 'clobbered'"
            ).fetchone()[0],
        )
        con.close()

    def _incident_bed(self):
        """The exact shape of yesterday: a live DB stamped at 49, curated rows
        carrying deliberate-read signal, and the real v49_to_v50 pending."""
        runner = _stage_runner(self.tmp / "incident", 50)
        db = _live_db(self.tmp, 49, memory_rows=1359)
        con = sqlite3.connect(db)
        con.execute("UPDATE memory SET deliberate_count = 3")
        con.commit()
        con.close()
        (runner.parent / "migrations" / "v49_to_v50.sql").write_text(
            (_MIGRATIONS_DIR / "v49_to_v50.sql").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        return runner, db

    def test_the_real_v49_to_v50_is_stopped_on_a_live_database(self):
        runner, db = self._incident_bed()
        result = _run_bootstrap(runner, db)

        # Printed so `pytest -s` shows the refusal a user would actually read;
        # the assertions below pin only the parts that must not drift.
        print(result.stderr)

        self.assertEqual(1, result.returncode)
        self.assertIn("BLOCKED: migration chain v49..v50", result.stderr)
        self.assertIn("v49_to_v50: UPDATE on `memory`", result.stderr)
        self.assertIn("1359 row(s) at risk", result.stderr)
        self.assertIn("gaia migrate apply --consent-chain v49..v50", result.stderr)

        con = sqlite3.connect(db)
        self.assertEqual(
            49, con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        )
        self.assertEqual(
            1359,
            con.execute("SELECT COUNT(*) FROM memory WHERE deliberate_count = 3").fetchone()[0],
        )
        con.close()

    def test_naming_the_chain_is_the_way_through(self):
        runner, db = self._incident_bed()
        result = _run_bootstrap(runner, db, consent="v49..v50")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        con = sqlite3.connect(db)
        self.assertEqual(
            50, con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        )
        self.assertEqual(
            0,
            con.execute("SELECT COUNT(*) FROM memory WHERE deliberate_count != 0").fetchone()[0],
        )
        self.assertEqual(
            1359,
            con.execute("SELECT COUNT(*) FROM memory_deliberate_capture_v50").fetchone()[0],
        )
        con.close()

    def test_consent_for_another_chain_does_not_carry(self):
        runner, db = self._incident_bed()
        result = _run_bootstrap(runner, db, consent="v48..v50")

        self.assertEqual(1, result.returncode)
        self.assertIn("BLOCKED: migration chain v49..v50", result.stderr)

    def test_a_chain_with_several_data_migrations_asks_once(self):
        start = self.expected - 2
        db = _live_db(self.tmp, start, memory_rows=5)
        self._stage(f"v{start}_to_v{start + 1}.sql", "UPDATE memory SET description = 'a';\n")
        self._stage(
            f"v{start + 1}_to_v{self.expected}.sql",
            "DELETE FROM memory WHERE name = 'fixture_row_0';\n",
        )
        label = migration_guard.chain_label(start, self.expected)

        refused = _run_bootstrap(self.runner, db)
        self.assertEqual(1, refused.returncode)
        self.assertEqual(1, refused.stderr.count("BLOCKED"))
        self.assertIn(f"v{start}_to_v{start + 1}: UPDATE", refused.stderr)
        self.assertIn(f"v{start + 1}_to_v{self.expected}: DELETE", refused.stderr)

        applied = _run_bootstrap(self.runner, db, consent=label)
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        self.assertNotIn("BLOCKED", applied.stderr)
        con = sqlite3.connect(db)
        self.assertEqual(
            self.expected,
            con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
        )
        self.assertEqual(4, con.execute("SELECT COUNT(*) FROM memory").fetchone()[0])
        con.close()
        self.assertEqual(1, len(list((db.parent / "backups").glob("*.db"))))


if __name__ == "__main__":
    unittest.main()
