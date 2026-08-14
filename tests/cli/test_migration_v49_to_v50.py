"""Migration v49 -> v50: row age, the kernel's own access pair, and the one-shot
capture-then-reset of the deliberate-read axis.

The version carries four parts in one file, and the ORDER of the last two is a
safety property rather than a preference: the capture of the deliberate axis is
written one statement before that axis is zeroed, inside the single transaction
Section 3c of scripts/bootstrap_database.py opens per migration. What is covered
here:

* the capture and the reset walk EXACTLY the same universe of rows -- proved
  three ways at once (nothing missing, nothing extra, no value different) over a
  seeded corpus that includes every edge shape the predicate must not lose: a
  soft-deleted row, a row in a second workspace, a row with a counter but no
  timestamp, and a row with a timestamp but a zero counter;
* the reset writes ONLY deliberate_count/last_deliberate_at -- every other
  column, the row count, and memory_history are compared against a pre-image
  taken before the migration ran;
* the two predicates are textually identical in the file itself, which is the
  form the completeness argument takes (it cannot be re-verified after the fact:
  once the source column is zeroed there is nothing left to recount);
* a stamped version is never reopened, so the non-idempotent capture/reset pair
  cannot run twice -- demonstrated the only way that rules the damage out rather
  than exhibiting it: seed a deliberate value AFTER the migration, invoke the
  runner again, and watch the value survive;
* a capture-table collision aborts the whole migration and leaves the axis
  intact, which is the fail-safe direction the plain (non OR-IGNORE) INSERT was
  chosen for.

The runner is driven in-process with its expected schema version patched, so
these tests neither read nor depend on the live EXPECTED_SCHEMA_VERSION and
never touch the user's database.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import migration_guard  # noqa: E402

_BOOTSTRAP_PY = _REPO_ROOT / "scripts" / "bootstrap_database.py"
_SCHEMA_SQL = _REPO_ROOT / "gaia" / "store" / "schema.sql"
_MIGRATION = _REPO_ROOT / "scripts" / "migrations" / "v49_to_v50.sql"

_CAPTURE_TABLE = "memory_deliberate_capture_v50"
_NEW_COLUMNS = ("created_at", "kernel_count", "last_kernel_at")

# Columns the migration must leave byte-identical on every row.
_UNTOUCHED_COLUMNS = (
    "type", "description", "body", "origin_session_id", "updated_at",
    "class", "status", "project_ref", "deleted_at", "initiative", "audience",
    "injection_count", "last_injected_at",
)


def _load_bootstrap_module():
    spec = importlib.util.spec_from_file_location("gaia_bootstrap_v50", _BOOTSTRAP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_bootstrap(bootstrap, db: Path, workspace: Path, expected: int) -> int:
    """Invoke the real runner against ``db`` with a patched expected version.

    Patching the version rather than the file keeps the test independent of
    bin/cli/doctor.py, which is bumped in its own commit.
    """
    bootstrap.GAIA_DB = db
    bootstrap.SCHEMA_FILE = _SCHEMA_SQL
    bootstrap.WORKSPACE = workspace
    bootstrap._read_expected_schema_version = lambda: expected
    # v49_to_v50 reaches data, and the fixture below is seeded with rows, so
    # the runner's consent gate refuses it unattended. Naming it here is what
    # this test bed is: a deliberate application against a throwaway database.
    consent = {migration_guard.ENV_CONSENT: "v49_to_v50"}
    with mock.patch.dict(os.environ, consent):
        return bootstrap.main()


def _seed_corpus(db: Path) -> None:
    """Write the v49-shaped fixture: two workspaces, every edge shape.

    Seeded deliberate values are distinct per row so a mixed-up capture cannot
    pass by coincidence.
    """
    con = sqlite3.connect(str(db))
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('ws_a', 'ws_a')")
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('ws_b', 'ws_b')")
        rows = [
            # workspace, name, deleted_at, deliberate_count, last_deliberate_at
            ("ws_a", "plain_signal", None, 7, "2026-08-01T10:00:00Z"),
            ("ws_a", "count_without_mark", None, 3, None),
            ("ws_a", "mark_without_count", None, 0, "2026-08-02T11:00:00Z"),
            ("ws_a", "tombstoned_signal", "2026-07-01T00:00:00Z", 5, "2026-08-03T12:00:00Z"),
            ("ws_b", "other_workspace_signal", None, 2, "2026-08-04T13:00:00Z"),
            ("ws_a", "no_signal_control", None, 0, None),
            ("ws_b", "no_signal_control", None, 0, None),
        ]
        for workspace, name, deleted_at, count, mark in rows:
            con.execute(
                "INSERT INTO memory (workspace, name, type, description, body, "
                "updated_at, class, status, deleted_at, audience, "
                "injection_count, deliberate_count, last_injected_at, "
                "last_deliberate_at) "
                "VALUES (?, ?, 'atom', ?, ?, '2026-06-01T00:00:00Z', 'log', NULL, "
                "?, 'any', 11, ?, '2026-05-05T05:05:05Z', ?)",
                (workspace, name, f"desc {name}", f"body of {name}", deleted_at, count, mark),
            )
        con.commit()
    finally:
        con.close()


def _read_memory(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return {
            (r["workspace"], r["name"]): dict(r)
            for r in con.execute("SELECT * FROM memory")
        }
    finally:
        con.close()


def _query(db: Path, sql: str, params: tuple = ()) -> list:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _scalar(db: Path, sql: str) -> int:
    return _query(db, sql)[0][0]


class TestMigrationV49ToV50(unittest.TestCase):
    """End-to-end behaviour, driven through the real bootstrap runner."""

    def setUp(self):
        if not _MIGRATION.is_file():
            self.skipTest(f"migration not found at {_MIGRATION}")
        self.bootstrap = _load_bootstrap_module()

    def _build_v49(self, tmp: Path) -> Path:
        db = tmp / "under_test.db"
        rc = _run_bootstrap(self.bootstrap, db, tmp, 49)
        self.assertEqual(rc, 0, "bootstrap to v49 must succeed")
        self.assertEqual(_scalar(db, "SELECT MAX(version) FROM schema_version"), 49)
        _seed_corpus(db)
        return db

    def test_capture_matches_the_reset_universe_and_nothing_else_moves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db = self._build_v49(tmp)
            before = _read_memory(db)
            history_before = _scalar(db, "SELECT COUNT(*) FROM memory_history")

            expected_capture = {
                (w, n): (r["deliberate_count"], r["last_deliberate_at"])
                for (w, n), r in before.items()
                if r["deliberate_count"] != 0 or r["last_deliberate_at"] is not None
            }
            self.assertEqual(len(expected_capture), 5, "fixture must carry 5 signal rows")

            rc = _run_bootstrap(self.bootstrap, db, tmp, 50)
            self.assertEqual(rc, 0, "migration to v50 must succeed")
            self.assertEqual(_scalar(db, "SELECT MAX(version) FROM schema_version"), 50)

            captured = {
                (w, n): (c, m)
                for w, n, c, m in _query(
                    db,
                    f"SELECT workspace, name, deliberate_count, last_deliberate_at "
                    f"FROM {_CAPTURE_TABLE}",
                )
            }
            # Three-way: nothing missing, nothing extra, no value different.
            self.assertEqual(captured, expected_capture)

            # The axis is zero everywhere -- including the rows the predicate
            # reached only through one of its two arms.
            self.assertEqual(
                _scalar(
                    db,
                    "SELECT COUNT(*) FROM memory WHERE deliberate_count != 0 "
                    "OR last_deliberate_at IS NOT NULL",
                ),
                0,
            )

            after = _read_memory(db)
            self.assertEqual(set(after), set(before), "no row created or destroyed")
            for key, row in after.items():
                for column in _UNTOUCHED_COLUMNS:
                    self.assertEqual(
                        row[column], before[key][column],
                        f"{column} moved on {key}",
                    )
                self.assertIsNone(row["created_at"], "no fabricated birth date")
                self.assertEqual(row["kernel_count"], 0)
                self.assertIsNone(row["last_kernel_at"])

            self.assertEqual(
                _scalar(db, "SELECT COUNT(*) FROM memory_history"), history_before,
                "the narrow reset must not trip trg_memory_history",
            )

            # A row that never carried signal is neither captured nor altered.
            self.assertNotIn(("ws_a", "no_signal_control"), captured)

    def test_stamped_version_is_never_reopened(self):
        """The capture/reset pair is not idempotent; the ledger is what makes
        that safe. Seeding AFTER the migration and re-running the runner is the
        demonstration that rules the damage out -- re-applying the raw file
        would only exhibit it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db = self._build_v49(tmp)
            self.assertEqual(_run_bootstrap(self.bootstrap, db, tmp, 50), 0)

            con = sqlite3.connect(str(db))
            try:
                con.execute(
                    "UPDATE memory SET deliberate_count = 42, "
                    "last_deliberate_at = '2026-09-09T09:09:09Z' "
                    "WHERE workspace = 'ws_a' AND name = 'plain_signal'"
                )
                con.commit()
            finally:
                con.close()
            capture_before = _query(
                db, f"SELECT workspace, name, deliberate_count, last_deliberate_at, "
                    f"captured_at FROM {_CAPTURE_TABLE} ORDER BY workspace, name"
            )

            self.assertEqual(_run_bootstrap(self.bootstrap, db, tmp, 50), 0)

            survived = _query(
                db,
                "SELECT deliberate_count, last_deliberate_at FROM memory "
                "WHERE workspace = 'ws_a' AND name = 'plain_signal'",
            )
            self.assertEqual(survived[0], (42, "2026-09-09T09:09:09Z"),
                             "fresh signal must survive a second runner invocation")
            self.assertEqual(
                _query(
                    db, f"SELECT workspace, name, deliberate_count, "
                        f"last_deliberate_at, captured_at FROM {_CAPTURE_TABLE} "
                        f"ORDER BY workspace, name"
                ),
                capture_before,
                "the capture must not be overwritten",
            )

    def test_capture_conflict_aborts_before_any_reset(self):
        """A plain INSERT (not OR IGNORE / OR REPLACE) is deliberate: a
        collision means something was already captured, and the only safe
        outcome is to fail with the axis untouched."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db = self._build_v49(tmp)
            # v50_to_v51 removed the capture table from schema.sql, so a DB
            # replayed from it no longer carries the table this collision needs.
            # Creating it here with the migration's own DDL reproduces the state
            # the collision describes -- something was already captured -- while
            # leaving v49_to_v50.sql the CREATE it performs for itself.
            con = sqlite3.connect(str(db))
            try:
                con.execute(
                    f"CREATE TABLE IF NOT EXISTS {_CAPTURE_TABLE} ("
                    "workspace TEXT NOT NULL, name TEXT NOT NULL, "
                    "deliberate_count INTEGER NOT NULL, last_deliberate_at TEXT, "
                    "captured_at TEXT NOT NULL, PRIMARY KEY (workspace, name))"
                )
                con.execute(
                    f"INSERT INTO {_CAPTURE_TABLE} VALUES "
                    "('ws_a', 'plain_signal', 999, NULL, '2026-01-01T00:00:00Z')"
                )
                con.commit()
            finally:
                con.close()

            self.assertEqual(_run_bootstrap(self.bootstrap, db, tmp, 50), 1)
            self.assertEqual(
                _scalar(db, "SELECT MAX(version) FROM schema_version"), 49,
                "a failed migration must not stamp the ledger",
            )
            self.assertEqual(
                _query(
                    db,
                    "SELECT deliberate_count, last_deliberate_at FROM memory "
                    "WHERE workspace = 'ws_a' AND name = 'plain_signal'",
                )[0],
                (7, "2026-08-01T10:00:00Z"),
                "rollback must leave the axis exactly as it was",
            )


class TestMigrationV49ToV50Shape(unittest.TestCase):
    """Static guards on the file itself. The completeness of the capture is a
    property of the TEXT -- it cannot be re-derived from the data afterwards."""

    def setUp(self):
        if not _MIGRATION.is_file():
            self.skipTest(f"migration not found at {_MIGRATION}")
        self.text = _MIGRATION.read_text()
        self.statements = [
            s.strip() for s in
            "\n".join(
                line for line in self.text.splitlines()
                if not line.lstrip().startswith("--")
            ).split(";")
            if s.strip()
        ]

    def _statement_starting_with(self, prefix: str) -> str:
        matches = [s for s in self.statements if s.upper().startswith(prefix)]
        self.assertEqual(len(matches), 1, f"expected exactly one {prefix} statement")
        return matches[0]

    def test_capture_and_reset_share_one_predicate(self):
        insert = self._statement_starting_with("INSERT INTO")
        update = self._statement_starting_with("UPDATE MEMORY")
        where_re = re.compile(r"\bWHERE\b(.*)$", re.IGNORECASE | re.DOTALL)
        capture_where = where_re.search(insert).group(1).strip()
        reset_where = where_re.search(update).group(1).strip()
        self.assertEqual(
            capture_where, reset_where,
            "capture and reset must walk the same universe of rows: any "
            "difference silently erases rows nobody wrote down first",
        )
        for forbidden in ("deleted_at", "workspace ="):
            self.assertNotIn(
                forbidden, capture_where,
                "the predicate must not narrow by lifecycle or workspace",
            )

    def test_reset_writes_only_the_deliberate_pair(self):
        update = self._statement_starting_with("UPDATE MEMORY")
        assigned = set(
            re.findall(r"(\w+)\s*=", update.split("WHERE")[0].split("SET", 1)[1])
        )
        self.assertEqual(assigned, {"deliberate_count", "last_deliberate_at"})

    def test_one_column_addition_per_line(self):
        """Both bootstrap layers (Section 1.5 reconcile, the ADD COLUMN
        idempotency filter) parse these line by line."""
        lines = [
            line for line in self.text.splitlines()
            if line.startswith("ALTER TABLE")
        ]
        self.assertEqual(len(lines), len(_NEW_COLUMNS))
        for line, column in zip(lines, _NEW_COLUMNS):
            self.assertRegex(line, rf"^ALTER TABLE memory ADD COLUMN {column}\b.*;$")

    def test_capture_precedes_the_reset(self):
        insert_at = self.text.index(f"INSERT INTO {_CAPTURE_TABLE}")
        update_at = self.text.index("UPDATE memory")
        self.assertLess(
            insert_at, update_at,
            "the capture must be written before the reset -- same file, "
            "therefore same transaction, therefore no window between them",
        )


if __name__ == "__main__":
    unittest.main()
